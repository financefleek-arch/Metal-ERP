"""F1b-1 — push_readiness / enqueue_push_sales / process_push_result /
routers, integration level (DB session + HTTP).

Setup: a firm registers normally (`/api/auth/register` — tenant + owner
login), a platform-admin provisions that same tenant's companion agent +
Tally company (mirroring how the Ops console actually links a real firm),
and the firm's own login creates + finalizes an invoice. Party/item
`tally_guid` is set directly via the session fixture to simulate "came
from an F1a pull" without re-running the whole masters-pull flow.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.services.tally.installer as installer_mod
from app.db import SessionLocal
from app.main import app
from app.models import BackupShop, Invoice, Item, Party, TallyCompany, TallySyncJob
from app.services.tally.jobs import enqueue_push_sales
from app.services.tally.push import process_push_result
from app.services.tally.push_readiness import push_blockers
from tools.make_platform_admin import run as make_admin


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_installer_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provisioning also builds + caches an installer zip — not this
    slice's concern, stub it out (no real agent build dir / R2 in tests).
    Mirrors `test_tally_agent.py`'s own fixture."""

    def _fake_build(session, shop, *, plaintext_key):  # type: ignore[no-untyped-def]
        shop.installer_r2_key = f"installers/{shop.id}.zip"
        session.flush()
        return shop.installer_r2_key

    monkeypatch.setattr(installer_mod, "build_and_cache_installer", _fake_build)


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _register_firm(client: TestClient, email: str) -> tuple[str, str]:
    """Returns (token, tenant_id)."""
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "F1b Traders", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers=_h(token)).json()
    return token, me["tenant_id"]


def _admin_token(client: TestClient, email: str = "ops@f1b.example.com") -> str:
    with SessionLocal() as s:
        make_admin(s, email=email, password="ops-s3cret-pass")
        s.commit()
    r = client.post("/api/auth/login", json={"email": email, "password": "ops-s3cret-pass"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _provision_agent(client: TestClient, admin_tok: str, firm_id: str) -> str:
    r = client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=_h(admin_tok))
    assert r.status_code == 201, r.text
    return r.json()["shop_id"]


def _link_company(
    client: TestClient, admin_tok: str, firm_id: str, *, sales_ledger: str | None = "Sales Accounts"
) -> None:
    _provision_agent(client, admin_tok, firm_id)
    r = client.post(
        f"/api/admin/firms/{firm_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F1b Traders"},
    )
    assert r.status_code == 200, r.text
    if sales_ledger:
        r = client.put(
            f"/api/admin/firms/{firm_id}/tally/company/ledger-map",
            headers=_h(admin_tok),
            json={"sales_ledger": sales_ledger},
        )
        assert r.status_code == 200, r.text


def _mark_agent_online_and_connected(shop_id: str) -> None:
    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        shop.last_checkin_at = datetime.now(UTC)
        shop.last_tally_status = "connected"
        s.commit()


def _party(client: TestClient, h: dict, name: str = "Anand Metals") -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _link_party_to_tally(party_id: str, guid: str = "party-guid-1") -> None:
    with SessionLocal() as s:
        p = s.get(Party, party_id)
        p.tally_guid = guid
        s.commit()


def _link_item_to_tally(item_id: str, guid: str = "item-guid-1") -> None:
    with SessionLocal() as s:
        it = s.get(Item, item_id)
        it.tally_guid = guid
        s.commit()


def _finalized_invoice(
    client: TestClient, h: dict, party_id: str, *, description: str = "SS Thali 11in"
) -> tuple[str, str]:
    """Creates + finalizes a single-line invoice. Returns (invoice_id, item_id)."""
    d = client.post("/api/invoices", headers=h, json={"party_id": party_id}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": description, "quantity": "10", "unit_rate": "96.00"}]},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text

    with SessionLocal() as s:
        inv = s.get(Invoice, d["id"])
        line = [ln for ln in inv.lines if (ln.description or "").strip()][0]
        item_id = line.item_id
    return d["id"], item_id


# --------------------------------------------------------------------------
# push_blockers
# --------------------------------------------------------------------------


def test_blockers_no_company(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "b1@x.example.com")
    pid = _party(client, _h(tok))
    iid, _item_id = _finalized_invoice(client, _h(tok), pid)

    with SessionLocal() as s:
        inv = s.get(Invoice, iid)
        blockers = push_blockers(s, inv)
    codes = {b.code for b in blockers}
    assert "no_tally_company" in codes


def test_blockers_ledger_map_incomplete(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "b2@x.example.com")
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id, sales_ledger=None)  # no sales_ledger set

    pid = _party(client, _h(tok))
    iid, _item_id = _finalized_invoice(client, _h(tok), pid)

    with SessionLocal() as s:
        inv = s.get(Invoice, iid)
        blockers = push_blockers(s, inv)
    codes = {b.code for b in blockers}
    assert "ledger_map_incomplete" in codes


def test_blockers_party_and_item_not_linked(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "b3@x.example.com")
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id)

    pid = _party(client, _h(tok))
    iid, item_id = _finalized_invoice(client, _h(tok), pid)
    # neither party nor item linked yet

    with SessionLocal() as s:
        inv = s.get(Invoice, iid)
        blockers = push_blockers(s, inv)
    codes = {b.code for b in blockers}
    assert "party_not_linked" in codes
    assert "item_not_linked" in codes


def test_blockers_empty_when_everything_linked(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "b4@x.example.com")
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id)

    pid = _party(client, _h(tok))
    iid, item_id = _finalized_invoice(client, _h(tok), pid)
    _link_party_to_tally(pid)
    _link_item_to_tally(item_id)

    with SessionLocal() as s:
        inv = s.get(Invoice, iid)
        blockers = push_blockers(s, inv)
    assert blockers == []


# --------------------------------------------------------------------------
# enqueue + result processing
# --------------------------------------------------------------------------


def _setup_pushable_invoice(client: TestClient, email: str) -> tuple[str, str, str, str]:
    """Returns (token, tenant_id, invoice_id, shop_id) for a fully-linked,
    pushable invoice."""
    tok, tenant_id = _register_firm(client, email)
    admin_tok = _admin_token(client)
    shop_id = _provision_agent(client, admin_tok, tenant_id)
    r = client.post(
        f"/api/admin/firms/{tenant_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F1b Traders"},
    )
    assert r.status_code == 200
    r = client.put(
        f"/api/admin/firms/{tenant_id}/tally/company/ledger-map",
        headers=_h(admin_tok),
        json={"sales_ledger": "Sales Accounts"},
    )
    assert r.status_code == 200

    pid = _party(client, _h(tok))
    iid, item_id = _finalized_invoice(client, _h(tok), pid)
    _link_party_to_tally(pid)
    _link_item_to_tally(item_id)
    _mark_agent_online_and_connected(shop_id)
    return tok, tenant_id, iid, shop_id


def test_enqueue_push_sales_creates_job_and_outbox(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "e1@x.example.com")

    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["kind"] == "push_sales"
    assert job["entity_type"] == "invoice"
    assert job["entity_id"] == iid

    with SessionLocal() as s:
        from app.models import AgentOutboxItem

        outbox = s.scalar(select(AgentOutboxItem).where(AgentOutboxItem.shop_id == shop_id))
        assert outbox is not None
        assert outbox.payload["action"] == "push_sales"
        assert outbox.payload["job_id"] == job["id"]

        # the built voucher XML travels in the outbox payload — no second
        # round trip for the agent to fetch it
        voucher_xml = outbox.payload["voucher_xml"]
        assert voucher_xml.startswith("<?xml")
        assert "VCHTYPE=\"Sales\"" in voucher_xml
        assert "Anand Metals" in voucher_xml
        assert "Sales Accounts" in voucher_xml


def test_push_blocked_with_422_when_not_ready(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "e2@x.example.com")
    pid = _party(client, _h(tok))
    iid, _item_id = _finalized_invoice(client, _h(tok), pid)
    # no tally company at all

    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 422
    assert "Tally" in r.json()["detail"]


def test_push_status_endpoint_reports_blockers(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "e3@x.example.com")
    pid = _party(client, _h(tok))
    iid, _item_id = _finalized_invoice(client, _h(tok), pid)

    r = client.get(f"/api/tally/invoices/{iid}/push-status", headers=_h(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["pushable"] is False
    assert any(b["code"] == "no_tally_company" for b in body["blockers"])


def test_second_push_while_in_flight_409s(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "e4@x.example.com")

    r1 = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r1.status_code == 201

    r2 = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r2.status_code == 409


def test_push_blocked_when_tally_not_reachable(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "e5@x.example.com")
    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        shop.last_tally_status = "refused"
        s.commit()

    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 409
    assert "closed" in r.json()["detail"] or "Server" in r.json()["detail"]


# --------------------------------------------------------------------------
# process_push_result
# --------------------------------------------------------------------------


_TALLY_OK_RESPONSE = """<RESPONSE>
 <CREATED>1</CREATED>
 <ALTERED>0</ALTERED>
 <DELETED>0</DELETED>
 <LASTVCHID>5</LASTVCHID>
 <LASTMID>0</LASTMID>
 <EXCEPTIONS>0</EXCEPTIONS>
</RESPONSE>"""

_TALLY_LINEERROR_RESPONSE = """<RESPONSE>
 <LINEERROR>Could not find Ledger 'Sales Accounts'</LINEERROR>
 <CREATED>0</CREATED>
 <EXCEPTIONS>1</EXCEPTIONS>
</RESPONSE>"""


def test_process_push_result_ok_writes_tally_link(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "p1@x.example.com")
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    job_id = r.json()["id"]

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(s, job, ok=True, tally_response=_TALLY_OK_RESPONSE, agent_error=None)
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "ok"
        from app.models import TallyLink

        link = s.scalar(
            select(TallyLink).where(
                TallyLink.tenant_id == tenant_id,
                TallyLink.entity_type == "invoice",
                TallyLink.entity_id == iid,
            )
        )
        assert link is not None
        assert link.tally_guid  # LASTVCHID fallback or similar, non-empty
        assert link.last_pushed_at is not None


def test_process_push_result_lineerror_fails_job(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "p2@x.example.com")
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    job_id = r.json()["id"]

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(
            s, job, ok=True, tally_response=_TALLY_LINEERROR_RESPONSE, agent_error=None
        )
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "error"
        assert "Sales Accounts" in job.error


def test_process_push_result_idempotent_on_terminal_job(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "p3@x.example.com")
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    job_id = r.json()["id"]

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(s, job, ok=True, tally_response=_TALLY_OK_RESPONSE, agent_error=None)
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(
            s, job, ok=False, tally_response=None, agent_error="should be ignored"
        )
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "ok"  # unchanged by the second call


def test_process_push_result_missing_response_fails_job(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "p4@x.example.com")
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    job_id = r.json()["id"]

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(s, job, ok=True, tally_response=None, agent_error=None)
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "error"
        assert "tally_response" in job.error


# --------------------------------------------------------------------------
# idempotent re-enqueue on an already-linked, unchanged invoice
# --------------------------------------------------------------------------


def test_reenqueue_after_ok_push_is_a_noop(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "r1@x.example.com")
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    job_id = r.json()["id"]

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(s, job, ok=True, tally_response=_TALLY_OK_RESPONSE, agent_error=None)
        s.commit()

    # direct service call (bypasses the in-flight-job 409 since the job is terminal)
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        invoice = s.get(Invoice, iid)
        new_job = enqueue_push_sales(s, company, invoice)
        s.commit()
        assert new_job.id == job_id  # returned the prior ok job, no new one created


# --------------------------------------------------------------------------
# finalize does NOT push to Tally — push is an explicit action afterwards
# (POST /api/tally/invoices/{id}/push), never a finalize side effect.
# --------------------------------------------------------------------------


def _fully_linked_finalized_invoice(client: TestClient, email: str):
    """Register a firm, provision its agent + Tally company + ledger map,
    link the party + a pre-created item, then create + finalize an invoice
    for them. Returns (tok, tenant_id, invoice_id). Everything a push needs
    is in place — only the explicit push call is missing."""
    tok, tenant_id = _register_firm(client, email)
    admin_tok = _admin_token(client)
    shop_id = _provision_agent(client, admin_tok, tenant_id)
    r = client.post(
        f"/api/admin/firms/{tenant_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F1b Traders"},
    )
    assert r.status_code == 200
    r = client.put(
        f"/api/admin/firms/{tenant_id}/tally/company/ledger-map",
        headers=_h(admin_tok),
        json={"sales_ledger": "Sales Accounts"},
    )
    assert r.status_code == 200
    _mark_agent_online_and_connected(shop_id)

    pid = _party(client, _h(tok))
    _link_party_to_tally(pid)
    with SessionLocal() as s:
        s.add(
            Item(
                tenant_id=tenant_id,
                name="SS Thali 11in",
                name_normalized="ss thali 11in",
                tally_guid=f"item-guid-{email}",
            )
        )
        s.commit()

    d = client.post("/api/invoices", headers=_h(tok), json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=_h(tok),
        json={"lines": [{"description": "SS Thali 11in", "quantity": "10", "unit_rate": "96.00"}]},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=_h(tok))
    assert r.status_code == 200, r.text
    return tok, tenant_id, d["id"]


def _push_job_for(tenant_id: str, invoice_id: str):
    with SessionLocal() as s:
        return s.scalar(
            select(TallySyncJob).where(
                TallySyncJob.tenant_id == tenant_id,
                TallySyncJob.entity_type == "invoice",
                TallySyncJob.entity_id == invoice_id,
            )
        )


def test_finalize_never_enqueues_even_when_fully_pushable(client: TestClient) -> None:
    """Party + item linked, agent online, Tally reachable — everything a
    push needs. Finalize STILL does not enqueue a job; it's an explicit
    action now."""
    tok, tenant_id, iid = _fully_linked_finalized_invoice(client, "f1@x.example.com")
    assert _push_job_for(tenant_id, iid) is None

    # ...but the explicit push endpoint does enqueue it.
    r = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok))
    assert r.status_code == 201, r.text
    job = _push_job_for(tenant_id, iid)
    assert job is not None
    assert job.kind == "push_sales"
    assert job.status == "queued"


def test_finalize_never_enqueues_when_no_tally_company(client: TestClient) -> None:
    """No Tally company at all — finalize succeeds, no job (unchanged)."""
    tok, tenant_id = _register_firm(client, "f2@x.example.com")
    pid = _party(client, _h(tok))
    iid, _item_id = _finalized_invoice(client, _h(tok), pid)
    assert _push_job_for(tenant_id, iid) is None

"""F5d — purchase_push_blockers / enqueue_push_purchase / process_push_result
/ routers / approve-time hook, integration level (DB session + HTTP).

Mirrors `tests/test_invoice_tally_push_integration.py`'s structure (F1b-1)
— a firm registers, a platform-admin provisions the companion agent +
Tally company (mirroring the Ops console), and the firm's own login
uploads + approves the Sugal Foods bill fixture. Strict mode means the
approved bill's supplier/items are staged-new and NOT yet Tally-linked —
tests explicitly link them via `Party.tally_guid`/`Item.tally_guid` to
simulate "already pulled from Tally" where a pushable state is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.services.tally.installer as installer_mod
from app.db import SessionLocal
from app.main import app
from app.models import BackupShop, InwardBill, Item, Party, TallySyncJob
from app.services.tally.jobs import enqueue_push_purchase
from app.services.tally.push import process_push_result
from app.services.tally.push_readiness import is_pushable, purchase_push_blockers
from tests.inward.conftest import SUGAL_PDF
from tools.make_platform_admin import run as make_admin


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_installer_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_build(session, shop, *, plaintext_key):  # type: ignore[no-untyped-def]
        shop.installer_r2_key = f"installers/{shop.id}.zip"
        session.flush()
        return shop.installer_r2_key

    monkeypatch.setattr(installer_mod, "build_and_cache_installer", _fake_build)


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _register_firm(client: TestClient, email: str) -> tuple[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "F5d Traders", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers=_h(token)).json()
    return token, me["tenant_id"]


def _enable_inward(client: TestClient, token: str, tenant_id: str) -> None:
    from app.models import Tenant

    with SessionLocal() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.id == tenant_id))
        assert tenant is not None
        tenant.ext_inward_import = True
        s.commit()


def _seed_hsn() -> None:
    from app.models import HsnCode

    with SessionLocal() as s:
        if s.get(HsnCode, "21069092") is None:
            s.add(
                HsnCode(
                    code="21069092",
                    description="Food preparations n.e.s. — sharbat / syrup",
                    chapter="21",
                    default_gst_rate=18.0,
                )
            )
            s.commit()


def _admin_token(client: TestClient, email: str = "ops@f5d.example.com") -> str:
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
    client: TestClient,
    admin_tok: str,
    firm_id: str,
    *,
    purchase_ledger: str | None = "Purchase Accounts",
    round_off_ledger: str | None = "Round Off",
) -> None:
    _provision_agent(client, admin_tok, firm_id)
    r = client.post(
        f"/api/admin/firms/{firm_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F5d Traders"},
    )
    assert r.status_code == 200, r.text
    ledger_map: dict[str, str] = {}
    if purchase_ledger:
        ledger_map["purchase_ledger"] = purchase_ledger
    if round_off_ledger:
        ledger_map["round_off_ledger"] = round_off_ledger
    if ledger_map:
        r = client.put(
            f"/api/admin/firms/{firm_id}/tally/company/ledger-map",
            headers=_h(admin_tok),
            json=ledger_map,
        )
        assert r.status_code == 200, r.text


def _mark_agent_online_and_connected(shop_id: str) -> None:
    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        shop.last_checkin_at = datetime.now(UTC)
        shop.last_tally_status = "connected"
        s.commit()


def _upload_and_approve_sugal(client: TestClient, headers: dict[str, str]) -> str:
    r = client.post(
        "/api/inward-bills",
        headers=headers,
        files={
            "files": (
                "sugal-foods-INV2526-5667.pdf",
                SUGAL_PDF.read_bytes(),
                "application/pdf",
            )
        },
    )
    assert r.status_code == 201, r.text
    bill_id = r.json()[0]["id"]
    r = client.post(f"/api/inward-bills/{bill_id}/approve", headers=headers)
    assert r.status_code == 200, r.text
    return bill_id


def _link_bill_masters(bill_id: str) -> None:
    """Link the approved bill's supplier + every line's item to Tally, as
    if a masters pull had already brought them in — the strict-mode
    precondition for a live push.
    """
    with SessionLocal() as s:
        bill = s.get(InwardBill, bill_id)
        assert bill is not None
        assert bill.matched_party_id is not None
        party = s.get(Party, bill.matched_party_id)
        assert party is not None
        party.tally_guid = "party-guid-sugal"
        for line in bill.lines:
            assert line.matched_item_id is not None
            item = s.get(Item, line.matched_item_id)
            assert item is not None
            item.tally_guid = f"item-guid-{line.sl_no}"
        s.commit()


# --------------------------------------------------------------------------
# purchase_push_blockers
# --------------------------------------------------------------------------


def test_blockers_no_company(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "pb1@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    bill_id = _upload_and_approve_sugal(client, _h(tok))

    with SessionLocal() as s:
        bill = s.get(InwardBill, bill_id)
        blockers = purchase_push_blockers(s, bill)
    codes = {b.code for b in blockers}
    assert "no_tally_company" in codes


def test_blockers_ledger_map_incomplete(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "pb2@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id, purchase_ledger=None, round_off_ledger=None)

    bill_id = _upload_and_approve_sugal(client, _h(tok))

    with SessionLocal() as s:
        bill = s.get(InwardBill, bill_id)
        blockers = purchase_push_blockers(s, bill)
    codes = {b.code for b in blockers}
    assert "ledger_map_incomplete" in codes


def test_blockers_party_and_item_unlinked_are_pending_create_not_blocking(
    client: TestClient,
) -> None:
    """F5-e (2026-09-09): the Sugal Foods bill's supplier + all 12 items
    are staged-NEW at approve time (empty catalogue) — this is no longer
    a hard block. It surfaces as informational `pending_master_create`
    entries (so the UI can say "will create N new masters") and the bill
    stays pushable — confirmed live to be the *expected common case* for
    a first-time vendor, not a rare edge case worth blocking on.
    """
    tok, tenant_id = _register_firm(client, "pb3@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id)

    bill_id = _upload_and_approve_sugal(client, _h(tok))
    # no _link_bill_masters() — everything still unlinked

    with SessionLocal() as s:
        bill = s.get(InwardBill, bill_id)
        blockers = purchase_push_blockers(s, bill)
    assert is_pushable(blockers)
    codes = {b.code for b in blockers}
    assert codes == {"pending_master_create"}
    messages = " ".join(b.message for b in blockers)
    assert "SUGAL FOODS" in messages
    assert "Monin" in messages


def test_blockers_not_approved(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "pb4@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id)

    r = client.post(
        "/api/inward-bills",
        headers=_h(tok),
        files={
            "files": (
                "sugal-foods-INV2526-5667.pdf",
                SUGAL_PDF.read_bytes(),
                "application/pdf",
            )
        },
    )
    bill_id = r.json()[0]["id"]  # uploaded, not approved

    with SessionLocal() as s:
        bill = s.get(InwardBill, bill_id)
        blockers = purchase_push_blockers(s, bill)
    codes = {b.code for b in blockers}
    assert "not_approved" in codes


def test_blockers_empty_when_everything_linked(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "pb5@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    _link_company(client, admin_tok, tenant_id)

    bill_id = _upload_and_approve_sugal(client, _h(tok))
    _link_bill_masters(bill_id)

    with SessionLocal() as s:
        bill = s.get(InwardBill, bill_id)
        blockers = purchase_push_blockers(s, bill)
    assert blockers == []


# --------------------------------------------------------------------------
# enqueue + result processing
# --------------------------------------------------------------------------


def _setup_pushable_bill(client: TestClient, email: str) -> tuple[str, str, str, str]:
    """Returns (token, tenant_id, bill_id, shop_id) for a fully-linked,
    pushable approved bill."""
    tok, tenant_id = _register_firm(client, email)
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    shop_id = _provision_agent(client, admin_tok, tenant_id)
    r = client.post(
        f"/api/admin/firms/{tenant_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F5d Traders"},
    )
    assert r.status_code == 200
    r = client.put(
        f"/api/admin/firms/{tenant_id}/tally/company/ledger-map",
        headers=_h(admin_tok),
        json={"purchase_ledger": "Purchase Accounts", "round_off_ledger": "Round Off"},
    )
    assert r.status_code == 200

    bill_id = _upload_and_approve_sugal(client, _h(tok))
    _link_bill_masters(bill_id)
    _mark_agent_online_and_connected(shop_id)
    return tok, tenant_id, bill_id, shop_id


def test_enqueue_push_purchase_creates_job_and_outbox_with_voucher_xml(
    client: TestClient,
) -> None:
    from app.models import AgentOutboxItem, TallyCompany

    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pe1@x.example.com")

    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        bill = s.get(InwardBill, bill_id)
        job = enqueue_push_purchase(s, company, bill)
        s.commit()
        job_id = job.id

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job is not None
        assert job.kind == "push_purchase"
        assert job.entity_type == "inward_bill"
        assert job.entity_id == bill_id
        assert job.status == "queued"

        outbox = s.scalar(
            select(AgentOutboxItem).where(AgentOutboxItem.id == job.outbox_item_id)
        )
        assert outbox is not None
        assert outbox.payload["action"] == "push_purchase"
        voucher_xml = outbox.payload["voucher_xml"]
        assert voucher_xml.startswith("<?xml")
        assert 'VCHTYPE="Purchase"' in voucher_xml
        assert "SUGAL FOODS" in voucher_xml
        assert "Purchase Accounts" in voucher_xml


def test_enqueue_push_purchase_is_idempotent_on_unchanged_bill(client: TestClient) -> None:
    from app.models import TallyCompany

    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pe2@x.example.com")

    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        bill = s.get(InwardBill, bill_id)
        job1 = enqueue_push_purchase(s, company, bill)
        s.commit()
        job1_id = job1.id
        # simulate a successful push completing
        process_push_result(
            s,
            s.get(TallySyncJob, job1_id),
            ok=True,
            tally_response="<RESPONSE><CREATED>1</CREATED><LASTVCHID>9</LASTVCHID></RESPONSE>",
            agent_error=None,
        )
        s.commit()

    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        bill = s.get(InwardBill, bill_id)
        job2 = enqueue_push_purchase(s, company, bill)
        s.commit()
        # no new job created — same job returned
        assert job2.id == job1_id


def test_process_push_result_ok_writes_tally_link(client: TestClient) -> None:
    from app.models import TallyCompany, TallyLink

    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pe3@x.example.com")

    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        bill = s.get(InwardBill, bill_id)
        job = enqueue_push_purchase(s, company, bill)
        s.commit()
        job_id = job.id

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(
            s,
            job,
            ok=True,
            tally_response=(
                "<RESPONSE><CREATED>13</CREATED><LASTVCHID>42</LASTVCHID>"
                "<EXCEPTIONS>0</EXCEPTIONS></RESPONSE>"
            ),
            agent_error=None,
        )
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "ok"
        link = s.scalar(
            select(TallyLink).where(
                TallyLink.entity_type == "inward_bill", TallyLink.entity_id == bill_id
            )
        )
        assert link is not None
        assert link.tally_guid == "42"


def test_process_push_result_line_error(client: TestClient) -> None:
    from app.models import TallyCompany

    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pe4@x.example.com")

    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        bill = s.get(InwardBill, bill_id)
        job = enqueue_push_purchase(s, company, bill)
        s.commit()
        job_id = job.id

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        process_push_result(
            s,
            job,
            ok=True,
            tally_response=(
                "<RESPONSE><CREATED>0</CREATED>"
                "<LINEERROR>Could not find stock item: 'Monin Mojito Mint Syrup 1000Ml'"
                "</LINEERROR></RESPONSE>"
            ),
            agent_error=None,
        )
        s.commit()

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "error"
        assert "Could not find stock item" in job.error


# --------------------------------------------------------------------------
# routers
# --------------------------------------------------------------------------


def test_push_status_route(client: TestClient) -> None:
    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pr1@x.example.com")
    r = client.get(f"/api/inward-bills/{bill_id}/tally/push-status", headers=_h(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pushable"] is True
    assert body["blockers"] == []


def test_push_route_422_on_blockers(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "pr2@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    bill_id = _upload_and_approve_sugal(client, _h(tok))
    r = client.post(f"/api/inward-bills/{bill_id}/tally/push", headers=_h(tok))
    assert r.status_code == 422


def test_push_route_creates_job(client: TestClient) -> None:
    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pr3@x.example.com")
    r = client.post(f"/api/inward-bills/{bill_id}/tally/push", headers=_h(tok))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "push_purchase"
    assert body["entity_type"] == "inward_bill"
    assert body["entity_id"] == bill_id


def test_push_route_404_for_other_tenant(client: TestClient) -> None:
    tok, tenant_id, bill_id, shop_id = _setup_pushable_bill(client, "pr4@x.example.com")
    other_tok, other_tenant_id = _register_firm(client, "pr4-other@x.example.com")
    _enable_inward(client, other_tok, other_tenant_id)
    r = client.post(f"/api/inward-bills/{bill_id}/tally/push", headers=_h(other_tok))
    assert r.status_code == 404


# --------------------------------------------------------------------------
# approve-time auto-enqueue hook
# --------------------------------------------------------------------------


def test_approve_auto_enqueues_push_with_auto_create_for_new_supplier_and_items(
    client: TestClient,
) -> None:
    """F5-e (2026-09-09): a brand-new-vendor bill (Sugal Foods — nothing
    pre-existing in Tally, matching the real bill that surfaced this gap)
    now auto-enqueues a push on approve, same as a fully-pre-linked bill
    would — the auto-create trigger set gets passed to the serializer, and
    the outbox payload's voucher XML contains real LEDGER/STOCKITEM
    ACTION="Create" blocks for the supplier + all 12 items alongside the
    voucher, in one envelope.
    """
    tok, tenant_id = _register_firm(client, "pa1@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    shop_id = _provision_agent(client, admin_tok, tenant_id)
    client.post(
        f"/api/admin/firms/{tenant_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F5d Traders"},
    )
    client.put(
        f"/api/admin/firms/{tenant_id}/tally/company/ledger-map",
        headers=_h(admin_tok),
        json={"purchase_ledger": "Purchase Accounts", "round_off_ledger": "Round Off"},
    )
    _mark_agent_online_and_connected(shop_id)

    bill_id = _upload_and_approve_sugal(client, _h(tok))
    # no _link_bill_masters() — supplier + all 12 items are staged-new,
    # exactly the real-world case that surfaced this gap.

    with SessionLocal() as s:
        job = s.scalar(
            select(TallySyncJob).where(
                TallySyncJob.entity_type == "inward_bill", TallySyncJob.entity_id == bill_id
            )
        )
        assert job is not None
        assert job.kind == "push_purchase"
        assert job.status == "queued"

        from app.models import AgentOutboxItem

        outbox = s.scalar(select(AgentOutboxItem).where(AgentOutboxItem.id == job.outbox_item_id))
        assert outbox is not None
        voucher_xml = outbox.payload["voucher_xml"]
        assert 'LEDGER NAME="SUGAL FOODS" ACTION="Create"' in voucher_xml
        # 1 supplier LEDGER + 12 item STOCKITEM creates + the voucher's own
        # ACTION="Create" = 14 ACTION="Create" occurrences total.
        assert voucher_xml.count('STOCKITEM NAME=') == 12
        assert voucher_xml.count('ACTION="Create"') == 14
        assert "Monin Mojito Mint Syrup" in voucher_xml
        assert 'VCHTYPE="Purchase"' in voucher_xml


def test_approve_does_not_fail_when_tally_unreachable(client: TestClient) -> None:
    tok, tenant_id = _register_firm(client, "pa2@x.example.com")
    _enable_inward(client, tok, tenant_id)
    _seed_hsn()
    admin_tok = _admin_token(client)
    _provision_agent(client, admin_tok, tenant_id)
    client.post(
        f"/api/admin/firms/{tenant_id}/tally/company",
        headers=_h(admin_tok),
        json={"company_name": "M/s F5d Traders"},
    )
    client.put(
        f"/api/admin/firms/{tenant_id}/tally/company/ledger-map",
        headers=_h(admin_tok),
        json={"purchase_ledger": "Purchase Accounts", "round_off_ledger": "Round Off"},
    )
    # agent never marked online/connected -> not reachable

    r = client.post(
        "/api/inward-bills",
        headers=_h(tok),
        files={
            "files": (
                "sugal-foods-INV2526-5667.pdf",
                SUGAL_PDF.read_bytes(),
                "application/pdf",
            )
        },
    )
    bill_id = r.json()[0]["id"]
    r = client.post(f"/api/inward-bills/{bill_id}/approve", headers=_h(tok))
    assert r.status_code == 200, r.text  # approve succeeds regardless

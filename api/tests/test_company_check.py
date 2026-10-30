"""S0.3 (backend half) - the agent checks the named company, the backend reports and relinks.

The agent compares a job's company with what the gateway has open before sending anything. Backend side:
jobs carry the company GUID, the agent's "not open" wait and hard stops are understood, a checkin's list
of open companies keeps each linked company's verdict current, and the owner can relink.
"""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentOutboxItem, BackupShop, TallyCompany, TallySyncJob
from app.services.tally.company_verify import OpenCompany, apply_open_companies
from app.services.tally.jobs import enqueue_pull_masters, record_agent_status
from app.services.tally.push import process_push_result
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _admin_token,
    _h,
    _setup_pushable_invoice,
    _stub_installer_build,  # autouse: no real agent build in tests
    client,
)

G1 = "167b31a2-6486-4396-8ee8-7fe38496052d"
G2 = "67baeb49-5454-418e-aad2-4ae28441e6d1"


def _company(tenant_id: str) -> TallyCompany:
    with SessionLocal() as s:
        return s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))


def _row(s, tenant_id: str) -> TallyCompany:
    return s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))


def _set(tenant_id: str, **fields) -> None:
    with SessionLocal() as s:
        c = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))
        for k, v in fields.items():
            setattr(c, k, v)
        s.commit()


def _checkin(shop_id: str, tenant_id: str, *open_companies: tuple[str, str]) -> None:
    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        apply_open_companies(
            s, shop, [OpenCompany(n, g, date(2026, 4, 1)) for n, g in open_companies]
        )
        s.commit()


def _outbox_payload(job_id: str) -> dict:
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        return s.get(AgentOutboxItem, job.outbox_item_id).payload


# --------------------------------------------------------------------------
# the job tells the agent which company
# --------------------------------------------------------------------------


def test_the_job_carries_the_guid_once_it_is_known(client: TestClient) -> None:
    _tok, tenant_id, _iid, _shop = _setup_pushable_invoice(client, "cc1@x.example.com")
    with SessionLocal() as s:
        job = enqueue_pull_masters(s, _row(s, tenant_id))
        s.commit()
        job_id = job.id
    first = _outbox_payload(job_id)
    assert first["company_name"] and "company_guid" not in first  # an older agent still works by name

    _set(tenant_id, company_guid=G1)
    with SessionLocal() as s:
        # the pull above is still in flight; expire it so a second one can be queued
        s.get(TallySyncJob, job_id).status = "error"
        s.commit()
        job = enqueue_pull_masters(s, _row(s, tenant_id))
        s.commit()
        second = job.id
    assert _outbox_payload(second)["company_guid"] == G1


# --------------------------------------------------------------------------
# waiting for the company, and the hard stops
# --------------------------------------------------------------------------


def test_company_not_open_is_a_wait_not_an_error(client: TestClient) -> None:
    _tok, tenant_id, iid, _shop = _setup_pushable_invoice(client, "cc2@x.example.com")
    job = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(_tok)).json()
    with SessionLocal() as s:
        record_agent_status(s, s.get(TallySyncJob, job["id"]), "company_not_open")
        s.commit()
        j = s.get(TallySyncJob, job["id"])
        assert j.status == "sent" and j.last_agent_status == "company_not_open"


def test_a_job_that_waited_too_long_names_the_company_and_what_is_open(client: TestClient) -> None:
    from app.services.tally.jobs import _expire_stuck_job

    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "cc3@x.example.com")
    name = _company(tenant_id).company_name
    _checkin(shop_id, tenant_id, ("Some Other Co", G2))
    job = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()
    with SessionLocal() as s:
        j = s.get(TallySyncJob, job["id"])
        j.status = "sent"
        j.last_agent_status = "company_not_open"
        j.created_at = datetime.now(UTC) - timedelta(minutes=20)
        assert _expire_stuck_job(s, j) is True
        s.commit()
        assert j.status == "error"
        assert f'"{name}" is not open' in j.error and '"Some Other Co"' in j.error
        # the voucher never left the agent for Tally while it waited, so nothing is unknown
        assert j.outcome_unknown is False


def _hard_stop(client, email, *, error_code):
    tok, tenant_id, iid, _shop = _setup_pushable_invoice(client, email)
    job = client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).json()
    with SessionLocal() as s:
        j = s.get(TallySyncJob, job["id"])
        process_push_result(
            s, j, ok=False, tally_response=None, agent_error="plain words", error_code=error_code
        )
        s.commit()
        return j.status, j.error, j.outcome_unknown


def test_a_company_stop_leaves_no_unknown_outcome(client: TestClient) -> None:
    for i, code in enumerate(("wrong_company", "company_renamed", "company_ambiguous")):
        status, error, unknown = _hard_stop(client, f"cc4{i}@x.example.com", error_code=code)
        assert (status, error, unknown) == ("error", "plain words", False)


def test_any_other_agent_failure_still_leaves_the_outcome_unknown(client: TestClient) -> None:
    status, _e, unknown = _hard_stop(client, "cc5@x.example.com", error_code=None)
    assert status == "error" and unknown is True


# --------------------------------------------------------------------------
# the checkin keeps the verdict current
# --------------------------------------------------------------------------


def test_checkin_learns_the_guid_by_itself(client: TestClient) -> None:
    _tok, tenant_id, _iid, shop_id = _setup_pushable_invoice(client, "cc6@x.example.com")
    name = _company(tenant_id).company_name
    _checkin(shop_id, tenant_id, (name, G1), ("Other", G2))

    c = _company(tenant_id)
    assert c.company_guid == G1 and c.verify_state == "verified" and c.books_from == date(2026, 4, 1)
    with SessionLocal() as s:
        shop = s.get(BackupShop, shop_id)
        assert [x["name"] for x in shop.tally_companies] == [name, "Other"]


def test_checkin_notices_a_rename(client: TestClient) -> None:
    _tok, tenant_id, _iid, shop_id = _setup_pushable_invoice(client, "cc7@x.example.com")
    _set(tenant_id, company_guid=G1)
    _checkin(shop_id, tenant_id, ("Brand New Name", G1))
    c = _company(tenant_id)
    assert c.verify_state == "renamed" and "Brand New Name" in c.verify_note


def test_checkin_notices_a_different_company_with_our_name(client: TestClient) -> None:
    _tok, tenant_id, _iid, shop_id = _setup_pushable_invoice(client, "cc8@x.example.com")
    name = _company(tenant_id).company_name
    _set(tenant_id, company_guid=G1)
    _checkin(shop_id, tenant_id, (name, G2))
    c = _company(tenant_id)
    assert c.verify_state == "different" and c.company_guid == G1  # the stored id is not overwritten


def test_an_empty_list_changes_no_verdict(client: TestClient) -> None:
    _tok, tenant_id, _iid, shop_id = _setup_pushable_invoice(client, "cc9@x.example.com")
    name = _company(tenant_id).company_name
    _checkin(shop_id, tenant_id, (name, G1))
    _checkin(shop_id, tenant_id)  # Tally answered with nothing open
    assert _company(tenant_id).verify_state == "verified"
    with SessionLocal() as s:
        assert s.get(BackupShop, shop_id).tally_companies == []


def test_the_same_checkin_every_minute_does_not_rewrite_the_company(client: TestClient) -> None:
    _tok, tenant_id, _iid, shop_id = _setup_pushable_invoice(client, "cc10@x.example.com")
    name = _company(tenant_id).company_name
    _checkin(shop_id, tenant_id, (name, G1))
    stamp = _company(tenant_id).verified_at
    _checkin(shop_id, tenant_id, (name, G1))
    assert _company(tenant_id).verified_at == stamp


# --------------------------------------------------------------------------
# a wrong company blocks the push, and the owner relinks
# --------------------------------------------------------------------------


def test_a_different_company_blocks_the_push_with_the_reason(client: TestClient) -> None:
    tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "cc11@x.example.com")
    name = _company(tenant_id).company_name
    _set(tenant_id, company_guid=G1)
    _checkin(shop_id, tenant_id, (name, G2))

    r = client.get(f"/api/tally/invoices/{iid}/push-status", headers=_h(tok)).json()
    assert r["pushable"] is False
    assert "company_different" in [b["code"] for b in r["blockers"]]
    assert client.post(f"/api/tally/invoices/{iid}/push", headers=_h(tok)).status_code >= 400


def test_relink_takes_the_new_name_and_keeps_the_guid(client: TestClient) -> None:
    _tok, tenant_id, iid, shop_id = _setup_pushable_invoice(client, "cc12@x.example.com")
    _set(tenant_id, company_guid=G1)
    _checkin(shop_id, tenant_id, ("Brand New Name", G1), ("Other", G2))
    admin = _admin_token(client)

    listed = client.get(f"/api/admin/firms/{tenant_id}/tally/company/open", headers=_h(admin)).json()
    assert {c["guid"] for c in listed} == {G1, G2}

    r = client.post(
        f"/api/admin/firms/{tenant_id}/tally/company/relink", json={"guid": G1}, headers=_h(admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_name"] == "Brand New Name" and body["company_guid"] == G1
    assert body["verify_state"] == "verified"


def test_relink_to_a_company_that_is_not_open_is_refused(client: TestClient) -> None:
    _tok, tenant_id, _iid, shop_id = _setup_pushable_invoice(client, "cc13@x.example.com")
    _checkin(shop_id, tenant_id, ("Other", G2))
    admin = _admin_token(client)
    r = client.post(
        f"/api/admin/firms/{tenant_id}/tally/company/relink", json={"guid": G1}, headers=_h(admin)
    )
    assert r.status_code == 422 and "not open" in r.json()["detail"]

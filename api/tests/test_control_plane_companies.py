"""Fleek's console reads a shop's Tally companies (read only), through the same functions the owner's app uses."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import PartyTallySnapshot, Tenant
from app.services.tally.companies import add_company
from tests.test_control_plane_firm import ORG, SECRET, _req, _secret, client, firm  # noqa: F401
from tests.test_tally_links import _party

BASE = f"/api/internal/control-plane/firms/{ORG}"


def _two_companies(firm_id: str) -> tuple[str, str]:
    with SessionLocal() as s:
        gst = add_company(s, firm_id, "Kumar GST")
        est = add_company(s, firm_id, "Kumar Estimate", kind="estimate")
        est.include_in_reminders = False
        est.visible_roles = ["accountant"]
        s.commit()
        return gst.id, est.id


def test_every_company_route_is_signed_and_only_for_a_linked_firm(client: TestClient, firm: str) -> None:
    gst, _ = _two_companies(firm)
    for suffix in ("/tally-companies", f"/tally-companies/{gst}/refill", f"/tally-companies/{gst}/jobs"):
        assert client.get(BASE + suffix).status_code == 401, suffix
        assert _req(client, "GET", BASE + suffix, secret="wrong").status_code == 401
    other = "/api/internal/control-plane/firms/not-linked"
    assert _req(client, "GET", other + "/tally-companies").status_code == 404


def test_the_operator_sees_every_company_with_who_may_see_it(client: TestClient, firm: str) -> None:
    gst, est = _two_companies(firm)
    r = _req(client, "GET", BASE + "/tally-companies")
    assert r.status_code == 200, r.text
    rows = {c["company_name"]: c for c in r.json()}
    assert set(rows) == {"Kumar GST", "Kumar Estimate"}
    assert rows["Kumar GST"]["is_default"] is True
    assert rows["Kumar Estimate"]["visible_roles"] == ["accountant"]  # the owner's view, not the accountant's
    assert rows["Kumar Estimate"]["include_in_reminders"] is False
    assert rows["Kumar Estimate"]["series"] != "Sales"  # its own number series


def test_a_switched_off_company_is_still_listed_for_support(client: TestClient, firm: str) -> None:
    _, est = _two_companies(firm)
    from app.services.tally.companies import get_company, set_active

    with SessionLocal() as s:
        set_active(s, get_company(s, firm, est), False)
        s.commit()
    rows = _req(client, "GET", BASE + "/tally-companies").json()
    assert {c["status"] for c in rows} == {"active", "disabled"}


def test_the_refill_status_and_jobs_of_a_company_are_readable(client: TestClient, firm: str) -> None:
    gst, _ = _two_companies(firm)
    with SessionLocal() as s:
        p = _party(s, firm, "Anand")
        s.add(
            PartyTallySnapshot(
                tenant_id=firm,
                company_id=gst,
                party_id=p.id,
                outstanding=Decimal("1000.00"),
                outstanding_as_of=date(2026, 10, 31),
            )
        )
        s.commit()
    r = _req(client, "GET", BASE + f"/tally-companies/{gst}/refill")
    assert r.status_code == 200, r.text
    assert r.json()["parties_with_balance"] == 1 and r.json()["outstanding_total"] == "1000.00"
    jobs = _req(client, "GET", BASE + f"/tally-companies/{gst}/jobs")
    assert jobs.status_code == 200 and jobs.json() == []


def test_another_firms_company_is_not_found(client: TestClient, firm: str) -> None:
    with SessionLocal() as s:
        other = Tenant(legal_name="Other Firm", city="Pune", fleek_org_id="someone-else")
        s.add(other)
        s.commit()
        other_id = other.id
    theirs, _ = _two_companies(other_id)
    assert _req(client, "GET", BASE + f"/tally-companies/{theirs}/refill").status_code == 404
    assert _req(client, "GET", BASE + f"/tally-companies/{theirs}/jobs").status_code == 404
    assert _req(client, "GET", BASE + "/tally-companies/not-a-company/refill").status_code == 404


def test_there_is_no_way_to_change_a_company_from_here(client: TestClient, firm: str) -> None:
    gst, _ = _two_companies(firm)
    for method, suffix in (
        ("POST", "/tally-companies"),
        ("PATCH", f"/tally-companies/{gst}"),
        ("POST", f"/tally-companies/{gst}/default"),
        ("POST", f"/tally-companies/{gst}/refresh"),
    ):
        assert _req(client, method, BASE + suffix, {}).status_code in (404, 405), (method, suffix)

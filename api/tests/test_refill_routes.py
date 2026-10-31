"""The refresh and status routes for the refill (routers/tally_companies.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import PartyTallySnapshot
from app.routers import tally_companies
from app.services.tally.companies import add_company
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_owner_companies import _agent, _colleague, _h
from tests.test_tally_links import _item, _party

BASE = "/api/tally/companies"


@pytest.fixture(autouse=True)
def _agent_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tally_companies, "assert_tally_reachable", lambda session, company: None)


def _company(tid: str) -> str:
    shop_id = _agent(tid)
    with SessionLocal() as s:
        c = add_company(s, tid, "GST", shop_id=shop_id)
        s.commit()
        return c.id


def test_refresh_queues_the_first_step_and_a_second_is_refused(client: TestClient) -> None:
    tok, tid = _register_firm(client, "rr1@x.example.com")
    cid = _company(tid)
    r = client.post(f"{BASE}/{cid}/refresh", headers=_h(tok), json={"months": 2})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "pull_vouchers" and r.json()["status"] == "queued"
    again = client.post(f"{BASE}/{cid}/refresh", headers=_h(tok), json={})
    assert again.status_code == 409

    st = client.get(f"{BASE}/{cid}/refill", headers=_h(tok)).json()
    assert st["running"] is True and st["step"] == "groups"


def test_months_are_bounded(client: TestClient) -> None:
    tok, tid = _register_firm(client, "rr2@x.example.com")
    cid = _company(tid)
    assert client.post(f"{BASE}/{cid}/refresh", headers=_h(tok), json={"months": 0}).status_code == 422
    assert client.post(f"{BASE}/{cid}/refresh", headers=_h(tok), json={"months": 13}).status_code == 422


def test_a_counter_user_cannot_refresh_but_anyone_who_sees_it_can_read_the_status(
    client: TestClient,
) -> None:
    tok, tid = _register_firm(client, "rr3@x.example.com")
    cid = _company(tid)
    viewer = _colleague(client, tid, "viewer")
    assert client.post(f"{BASE}/{cid}/refresh", headers=viewer, json={}).status_code == 403
    assert client.get(f"{BASE}/{cid}/refill", headers=viewer).status_code == 200


def test_another_firms_company_is_not_found(client: TestClient) -> None:
    _tok_a, tid_a = _register_firm(client, "rr4a@x.example.com")
    tok_b, _tid_b = _register_firm(client, "rr4b@x.example.com")
    cid = _company(tid_a)
    assert client.post(f"{BASE}/{cid}/refresh", headers=_h(tok_b), json={}).status_code == 404
    assert client.get(f"{BASE}/{cid}/refill", headers=_h(tok_b)).status_code == 404


def test_the_status_counts_what_the_company_holds(client: TestClient) -> None:
    tok, tid = _register_firm(client, "rr5@x.example.com")
    cid = _company(tid)
    with SessionLocal() as s:
        p = _party(s, tid, "Anand")
        s.add(
            PartyTallySnapshot(
                tenant_id=tid,
                company_id=cid,
                party_id=p.id,
                outstanding=Decimal("1000.00"),
                outstanding_as_of=date(2026, 10, 31),
            )
        )
        _item(s, tid, "Thali")
        s.commit()
    st = client.get(f"{BASE}/{cid}/refill", headers=_h(tok)).json()
    assert st["parties_with_balance"] == 1 and st["outstanding_total"] == "1000.00"
    assert st["running"] is False and st["items_with_stock"] == 0 and st["prices"] == 0

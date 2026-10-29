"""The daily roll-up a shop sends Fleek: events become counts and first times, established shops are not mistaken for stuck ones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActivityEvent, Tenant, User
from app.services import activity_rollup
from tests import test_account_onboarding as onboarding
from tests.test_account_onboarding import FakeFleek, _h, _signup

client = onboarding.client
fleek = onboarding.fleek


def _tenant() -> Tenant:
    with SessionLocal() as s:
        return s.scalars(select(Tenant)).one()


def test_a_new_shops_rollup_has_its_events_as_first_times_and_counts(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"})
    client.post("/api/parties", headers=_h(t), json={"legal_name": "Bharat Steel", "role": "customer"})
    with SessionLocal() as s:
        tenant = s.scalars(select(Tenant)).one()
        body = activity_rollup.build(s, tenant, started=activity_rollup.tracking_started(s))
    assert body["version"] == 1 and body["signed_up_at"] and "signed_up" in body["first"]
    assert "party_created" in body["first"] and body["counts_30d"]["party_created"] == 2
    assert "invoice_pdf_out" not in body["first"]            # a new shop's PDF must really have left the app
    assert body["active_days_30"] >= 1 and len(body["weekly_invoices"]) == activity_rollup.WEEKS


def test_an_established_shop_with_no_events_is_filled_from_its_data_and_counts_as_activated(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    party = client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"}).json()
    inv = client.post("/api/invoices", headers=_h(t), json={"party_id": party["id"]}).json()
    client.put(f"/api/invoices/{inv['id']}", headers=_h(t), json={"lines": [{"description": "Steel plate", "quantity": "1", "unit_rate": "100"}]})
    client.post(f"/api/invoices/{inv['id']}/finalize", headers=_h(t))
    with SessionLocal() as s:
        s.query(ActivityEvent).delete()          # as if the shop had been using the app before events were recorded
        tenant = s.scalars(select(Tenant)).one()
        tenant.created_at = datetime.now(UTC) - timedelta(days=90)
        s.commit()
        body = activity_rollup.build(s, tenant, started=datetime.now(UTC) - timedelta(days=1))
    assert {"party_created", "invoice_draft_created", "invoice_finalised", "invoice_pdf_out"} <= set(body["first"])


def test_last_seen_is_the_latest_across_the_shops_users_and_ignores_none(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    stamp = datetime.now(UTC) - timedelta(hours=3)
    with SessionLocal() as s:
        s.scalars(select(User)).one().last_seen_at = stamp
        s.commit()
        body = activity_rollup.build(s, s.scalars(select(Tenant)).one())
    assert body["last_seen_at"] is not None and body["last_seen_at"].startswith(stamp.date().isoformat())


def test_send_all_posts_one_signed_rollup_per_linked_shop_and_survives_a_failure(client: TestClient, fleek: FakeFleek, monkeypatch: pytest.MonkeyPatch) -> None:
    _signup(client, fleek)
    seen: list[tuple[str, str, Any]] = []

    def post(method: str, path: str, payload: Any = None, **_: Any) -> tuple[int, dict]:
        seen.append((method, path, payload))
        return 204, {}

    monkeypatch.setattr("app.services.activity_rollup.fleek_request", post)
    assert activity_rollup.send_all() == 1
    method, path, payload = seen[0]
    assert method == "POST" and path.endswith("/activity") and "/orgs/org-" in path and payload["version"] == 1

    def refuse(*_a: Any, **_k: Any) -> tuple[int, dict]:
        return 400, {}

    monkeypatch.setattr("app.services.activity_rollup.fleek_request", refuse)
    assert activity_rollup.send_all() == 0           # a refused roll-up is logged, not raised

"""The roll-up a shop sends Fleek (hourly): events become counts and first times, established shops are not mistaken for stuck ones."""

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


def test_an_unchanged_shop_is_skipped_each_hour_but_sent_again_after_a_day(client: TestClient, fleek: FakeFleek, monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time

    _signup(client, fleek)
    activity_rollup._last_sent.clear()
    seen: list[Any] = []
    monkeypatch.setattr("app.services.activity_rollup.fleek_request", lambda *a, **k: (seen.append(a), (204, {}))[1])
    assert activity_rollup.send_all() == 1
    assert activity_rollup.send_all() == 0  # the next hour: nothing new, nothing sent
    assert len(seen) == 1
    client.post("/api/parties", headers=_h(_login_token(client)), json={"legal_name": "Anil Traders", "role": "customer"})
    assert activity_rollup.send_all() == 1  # a new party changes the numbers, so it goes at once
    real = _time.time
    monkeypatch.setattr("app.services.activity_rollup.time.time", lambda: real() + 24 * 3600)
    assert activity_rollup.send_all() == 1  # a day on, even unchanged, it is refreshed


def _login_token(client: TestClient) -> str:
    r = client.post("/api/auth/login", json={"identifier": onboarding.MOBILE, "password": onboarding.SIGNUP["password"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_a_milestone_queues_a_send_that_goes_out_after_the_delay_and_only_once(client: TestClient, fleek: FakeFleek, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import activity

    activity._pending.clear()
    activity_rollup._last_sent.clear()
    t = _signup(client, fleek)  # "signed_up" is a milestone: the shop is queued
    tenant_id = _tenant().id
    assert tenant_id in activity._pending
    seen: list[Any] = []
    monkeypatch.setattr("app.services.activity_rollup.fleek_request", lambda *a, **k: (seen.append(a), (204, {}))[1])
    assert activity_rollup.send_due() == 0  # not due yet: the delay lets the request commit and a burst go out together
    real = _time_now()
    monkeypatch.setattr("app.services.activity.time.time", lambda: real + activity.NUDGE_DELAY + 1)
    assert activity_rollup.send_due() == 1 and len(seen) == 1
    assert activity_rollup.send_due() == 0  # taken off the queue

    client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"})
    assert tenant_id in activity._pending  # a first customer queues it again
    monkeypatch.setattr("app.services.activity.time.time", lambda: real + 2 * activity.NUDGE_DELAY + 2)
    assert activity_rollup.send_due() == 0  # sent under five minutes ago, so it waits...
    assert tenant_id in activity._pending  # ...and stays queued
    monkeypatch.setattr("app.services.activity_rollup.time.time", lambda: real + activity_rollup.NUDGE_MIN_GAP + 10)
    monkeypatch.setattr("app.services.activity.time.time", lambda: real + activity_rollup.NUDGE_MIN_GAP + 200)
    assert activity_rollup.send_due() == 1  # the gap has passed, the new numbers go


def test_browser_beacons_and_sign_ins_do_not_queue_a_send(client: TestClient, fleek: FakeFleek) -> None:
    from app.services import activity

    t = _signup(client, fleek)
    activity._pending.clear()
    client.post("/api/onboarding/activity", headers=_h(t), json={"key": "setup_viewed"})
    assert activity._pending == {}


def _time_now() -> float:
    import time as _t

    return _t.time()

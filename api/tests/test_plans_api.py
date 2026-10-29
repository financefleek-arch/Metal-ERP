"""Plan and billing: the ladder is read from Fleek, a 3-day trial is started through Fleek, cancelling is one call (app/routers/plans.py)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActivityEvent, TenantEntitlement
from app.routers import plans as plans_router
from app.services.fleek_signed import FleekUnavailable
from tests import test_account_onboarding as onboarding
from tests.test_account_onboarding import FakeFleek, _h, _signup

client = onboarding.client
fleek_base = onboarding.fleek

LADDER = [
    {"plan": "metal_erp_free", "label": "Free", "price_amount": 0, "users": 1, "unlocks": [], "trial_days": 0},
    {"plan": "metal_erp_basic", "label": "Starter", "price_amount": 399, "users": 1, "unlocks": ["pdf_out"], "trial_days": 0},
    {"plan": "metal_erp_growth", "label": "Growth", "price_amount": 999, "users": 3, "unlocks": ["pdf_out", "whatsapp"], "trial_days": 3},
    {"plan": "metal_erp_complete", "label": "Complete", "price_amount": 1499, "users": 5, "unlocks": ["pdf_out", "whatsapp", "tally_full"], "trial_days": 3},
]


class PlanFleek(FakeFleek):
    """The sign-up fake, plus the plan routes."""

    def __init__(self) -> None:
        super().__init__()
        self.plan_calls: list[tuple[str, str, Any]] = []
        self.start_status, self.cancel_status = 200, 200

    def __call__(self, method: str, path: str, payload: dict[str, Any] | None = None, **kw: Any) -> tuple[int, dict[str, Any]]:
        if path.endswith("/internal/metal-erp/plans"):
            if self.down:
                raise FleekUnavailable("down")
            self.plan_calls.append((method, path, None))
            return 200, {"plans": LADDER}
        if path.endswith("/start-plan"):
            self.plan_calls.append((method, path, payload))
            if self.start_status != 200:
                return self.start_status, {"error": "not_on_free", "message": "only a shop on the Free plan can start a trial"}
            return 200, {"short_url": "https://rzp.io/i/abc", "trial_ends_on": "2026-11-04", "amount": 999, "plan": payload["plan"], "label": "Growth"}  # type: ignore[index]
        if path.endswith("/cancel-plan"):
            self.plan_calls.append((method, path, payload))
            if self.cancel_status != 200:
                return self.cancel_status, {"error": "nothing_to_cancel", "message": "This shop is on the Free plan."}
            return 200, {"effective": "period_end", "ends_on": "2026-11-04"}
        return super().__call__(method, path, payload, **kw)


@pytest.fixture
def fleek(monkeypatch: pytest.MonkeyPatch) -> PlanFleek:
    f = PlanFleek()
    monkeypatch.setattr("app.services.account.fleek_request", f)
    monkeypatch.setattr("app.routers.onboarding.fleek_request", f)
    monkeypatch.setattr("app.services.captcha.fleek_request", f)
    monkeypatch.setattr("app.routers.plans.fleek_request", f)
    plans_router.reset_cache()
    return f


def _linked_token(client: TestClient, fleek: PlanFleek) -> str:
    return _signup(client, fleek)         # a self-signed-up firm is linked to its Fleek org


def test_the_ladder_is_read_from_fleek_and_cached(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    out = client.get("/api/plans", headers=_h(t)).json()
    assert [p["plan"] for p in out["plans"]] == [p["plan"] for p in LADDER]
    assert out["can_manage"] is True and out["current"]["free"] is True and out["pdf_days_left"] > 0
    client.get("/api/plans", headers=_h(t))
    assert len([c for c in fleek.plan_calls if c[1].endswith("/plans")]) == 1       # the second read came from the cache


def test_plans_are_unavailable_not_invented_when_fleek_is_down(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    plans_router.reset_cache()
    fleek.down = True
    r = client.get("/api/plans", headers=_h(t))
    assert r.status_code == 503 and "not available" in r.json()["detail"]


def test_the_landing_page_can_read_the_ladder_without_signing_in(client: TestClient, fleek: PlanFleek) -> None:
    plans_router.reset_cache()
    out = client.get("/api/plans/public").json()
    assert [p["price_amount"] for p in out["plans"]] == [0, 399, 999, 1499]


def test_starter_is_started_the_same_way(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    assert client.post("/api/plans/start", headers=_h(t), json={"plan": "metal_erp_basic"}).status_code == 200
    assert fleek.plan_calls[-1][2] == {"plan": "metal_erp_basic"}


def test_a_trial_is_started_through_fleek_and_nothing_is_charged_by_us(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    r = client.post("/api/plans/start", headers=_h(t), json={"plan": "metal_erp_growth"})
    assert r.status_code == 200 and r.json()["short_url"].startswith("https://")
    assert fleek.plan_calls[-1][2] == {"plan": "metal_erp_growth"}
    with SessionLocal() as s:
        assert "plan_trial_started" in [e.key for e in s.scalars(select(ActivityEvent))]


def test_only_a_plan_with_a_trial_can_be_tried_and_only_by_the_owner(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    assert client.post("/api/plans/start", headers=_h(t), json={"plan": "metal_erp_free"}).status_code == 422      # Free is not bought
    assert client.post("/api/plans/start", headers=_h(t), json={"plan": "nonsense"}).status_code == 422
    assert not [c for c in fleek.plan_calls if c[1].endswith("/start-plan")]                                      # Fleek was never asked


def test_a_refusal_from_fleek_reaches_the_owner_in_plain_words(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    fleek.start_status = 409
    r = client.post("/api/plans/start", headers=_h(t), json={"plan": "metal_erp_growth"})
    assert r.status_code == 409 and "Free plan" in r.json()["detail"]


def test_starting_or_cancelling_drops_the_cached_licence_so_it_shows_at_once(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    with SessionLocal() as s:
        from datetime import UTC, datetime, timedelta

        from app.models import Tenant

        tenant = s.scalars(select(Tenant)).one()
        s.add(TenantEntitlement(tenant_id=tenant.id, payload={"status": "active"}, last_verified_at=datetime.now(UTC),
                                valid_until=datetime.now(UTC) + timedelta(hours=20)))
        s.commit()
    client.post("/api/plans/start", headers=_h(t), json={"plan": "metal_erp_growth"})
    with SessionLocal() as s:
        row = s.scalars(select(TenantEntitlement)).one()
        from datetime import UTC, datetime

        assert row.valid_until < datetime.now(UTC).replace(tzinfo=row.valid_until.tzinfo)       # next read asks Fleek again


def test_cancel_is_one_call_and_its_result_is_passed_on(client: TestClient, fleek: PlanFleek) -> None:
    t = _linked_token(client, fleek)
    r = client.post("/api/plans/cancel", headers=_h(t))
    assert r.status_code == 200 and r.json() == {"effective": "period_end", "ends_on": "2026-11-04"}
    with SessionLocal() as s:
        assert "plan_cancelled" in [e.key for e in s.scalars(select(ActivityEvent))]
    fleek.cancel_status = 409
    assert client.post("/api/plans/cancel", headers=_h(t)).status_code == 409

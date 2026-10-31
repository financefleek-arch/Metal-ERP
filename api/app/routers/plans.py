"""Plan and billing: the ladder, the owner's 3-day free trial, and one-tap cancellation (docs/PLAN-shop-activity-intelligence.md section 8).

Fleek owns plans and prices, so nothing is hard-coded here: the ladder is read from Fleek (`GET /api/internal/metal-erp/plans`) and cached for a few
minutes. Starting a trial asks Fleek to move the shop to the plan and to open the hosted autopay page; the first charge is scheduled for the trial's
end, so nothing is charged earlier. Cancelling is one call, no questions. After either, the cached licence is dropped so the shop sees the change at once.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.deps import CurrentUser, SessionDep
from app.models import Tenant
from app.models._mixins import UserRole
from app.services import activity, entitlements, features
from app.services.fleek_signed import FleekUnavailable
from app.services.fleek_signed import request as fleek_request

router = APIRouter(prefix="/api/plans", tags=["plans"])

LADDER_PATH = "/api/internal/metal-erp/plans"
CACHE_SECONDS = 300
_cache: tuple[float, list[dict[str, Any]]] | None = None
UNAVAILABLE = "Plans are not available just now. Please try again in a minute."


def reset_cache() -> None:
    global _cache
    _cache = None


def ladder() -> list[dict[str, Any]]:
    """Free, then the paid plans cheapest first: [{plan, label, price_amount, users, unlocks, trial_days}]. 503 when Fleek cannot be asked."""
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        code, body = fleek_request("GET", LADDER_PATH)
    except FleekUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    plans = body.get("plans")
    if code != 200 or not isinstance(plans, list):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    _cache = (time.monotonic(), plans)
    return plans


def _tenant(session: SessionDep, user: CurrentUser) -> Tenant:
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None  # FK guarantees it
    return tenant


def _owner(user: CurrentUser) -> None:
    if user.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change the plan")


def _linked(tenant: Tenant) -> str:
    if not tenant.fleek_org_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This firm is not linked to Fleek yet")
    return tenant.fleek_org_id


class StartIn(BaseModel):
    plan: str


@router.get("/public")
def public_plans() -> dict[str, Any]:
    """The ladder for the signed-out landing page (prices are public). Same cache as the signed-in page."""
    return {"plans": ladder()}


@router.get("")
def get_plans(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """The ladder, what the firm is on now, and (on Free) how many days of PDF download are left."""
    tenant = _tenant(session, user)
    plans = ladder()
    licence = entitlements.current(session, tenant)
    free = features.is_free(session, tenant, licence)
    return {
        "plans": plans,
        "current": {
            "plan_key": licence.plan_key if licence else None,
            "plan_label": licence.plan_label if licence else None,
            "status": licence.status if licence else None,
            "trial_days_left": licence.trial_days_left if licence else None,
            "state": licence.state if licence else None,
            "grace_until": licence.grace_until if licence else None,
            "can_retry": licence.can_retry if licence else False,
            "free": free,
        },
        "pdf_days_left": features.pdf_days_left(tenant) if free else None,
        "can_manage": user.role == UserRole.owner and bool(tenant.fleek_org_id),
    }


@router.post("/start")
def start_plan(body: StartIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Start a paid plan: Growth and Complete are tried free for 3 days (nothing is charged until the trial ends); Starter has no trial and is charged when
    the owner approves the payment. -> {short_url, trial_ends_on, amount, label}: the owner opens `short_url` to approve autopay."""
    _owner(user)
    tenant = _tenant(session, user)
    org_id = _linked(tenant)
    if body.plan not in {p["plan"] for p in ladder() if p.get("price_amount", 0) > 0}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose one of the paid plans")
    try:
        code, out = fleek_request("POST", f"/api/internal/metal-erp/orgs/{org_id}/start-plan", {"plan": body.plan})
    except FleekUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    if code != 200:
        raise HTTPException(code if code in (404, 409, 502) else 502, out.get("message") or UNAVAILABLE)
    entitlements.invalidate(session, tenant)
    # The owner opened the payment page: an intent, not a plan. The plan begins when Fleek sees the autopay approved (the licence then says so).
    activity.record(session, tenant_id=tenant.id, user_id=user.id, key="plan_checkout_opened", props={"plan": body.plan})
    return out


@router.post("/check")
def check_payment(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """The owner is back from the payment page: ask Fleek (which asks Razorpay) whether the autopay was approved, then drop the cached licence so the
    page shows the result now instead of waiting on a webhook. -> {changed}."""
    _owner(user)
    tenant = _tenant(session, user)
    org_id = _linked(tenant)
    try:
        code, out = fleek_request("POST", f"/api/internal/metal-erp/orgs/{org_id}/check-payment", {})
    except FleekUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    if code != 200:
        raise HTTPException(code if code in (404, 409, 502) else 502, out.get("message") or UNAVAILABLE)
    entitlements.invalidate(session, tenant)
    return {"changed": bool(out.get("changed"))}


@router.post("/cancel")
def cancel_plan(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Cancel in one tap, no questions. A trial with no autopay, or a plan that holds no paid access (payment not completed, failed, ended), goes back
    to Free now; otherwise the plan runs to the end of the trial or paid month and nothing further is charged. -> {effective: "now"|"period_end", ends_on}."""
    _owner(user)
    tenant = _tenant(session, user)
    org_id = _linked(tenant)
    try:
        code, out = fleek_request("POST", f"/api/internal/metal-erp/orgs/{org_id}/cancel-plan", {})
    except FleekUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    if code != 200:
        raise HTTPException(code if code in (404, 409, 502) else 502, out.get("message") or UNAVAILABLE)
    entitlements.invalidate(session, tenant)
    activity.record(session, tenant_id=tenant.id, user_id=user.id, key="plan_cancelled")
    return out

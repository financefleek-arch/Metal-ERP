"""The Setup screen's data, and the owner's "ask Fleek to switch this on".

See docs/PLAN-onboarding-and-account.md 3.6.

Plans and prices are Fleek's business, so a shop does not buy a feature here: it asks, Fleek sees
the request in its admin, changes the plan or switches the feature on, and the next licence refresh
turns the card active.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.deps import CurrentUser, SessionDep
from app.models import Tenant
from app.models._mixins import UserRole
from app.services import onboarding
from app.services.fleek_signed import FleekUnavailable
from app.services.fleek_signed import request as fleek_request

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class DismissIn(BaseModel):
    dismissed: bool


class FeatureRequestIn(BaseModel):
    feature: str


def _tenant(session: SessionDep, user: CurrentUser) -> Tenant:
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None  # FK guarantees it
    return tenant


def _owner(user: CurrentUser) -> None:
    if user.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can do this")


@router.get("")
def get_overview(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return onboarding.overview(session, _tenant(session, user))


@router.put("/dismissed", status_code=status.HTTP_204_NO_CONTENT)
def set_dismissed(body: DismissIn, user: CurrentUser, session: SessionDep) -> None:
    from datetime import UTC, datetime

    _owner(user)
    _tenant(session, user).onboarding_dismissed_at = datetime.now(UTC) if body.dismissed else None


@router.post("/requests")
def request_feature(body: FeatureRequestIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Tell Fleek this shop wants a feature. Fleek is told first: an unseen request is worse than none."""
    _owner(user)
    tenant = _tenant(session, user)
    if body.feature not in onboarding.feature_keys():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown feature")
    if tenant.fleek_org_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This firm is not linked to Fleek yet")
    cards = onboarding.overview(session, tenant)["features"]
    title = next(c["title"] for c in cards if c["key"] == body.feature)
    try:
        code, _ = fleek_request(
            "POST",
            f"/api/internal/metal-erp/orgs/{tenant.fleek_org_id}/feature-requests",
            {"feature": body.feature, "title": title, "requested_by": user.label},
        )
    except FleekUnavailable:
        code = 0
    if code not in (200, 201):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We could not reach Fleek just now. Please try again in a minute.",
        )
    return {"feature": body.feature, "requested_at": onboarding.mark_requested(tenant, body.feature)}

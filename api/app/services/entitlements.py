"""A firm's licence, as the Fleek control plane reports it (docs/PLAN-fleek-control-plane.md 13.3-13.5).

Fleek owns the licence. This app only asks, remembers the answer, and applies two deliberately LENIENT
rules (owner decision 2026-10-20):

  * a firm with no `fleek_org_id` is unlimited, exactly as before it was linked (linking is the switch);
  * nothing that runs the business is ever blocked: not billing, viewing, PDFs, or the messages that go
    with an invoice. A lapsed licence only stops the proactive sends (payment reminders; campaigns when
    they exist) and shows a banner. A plan's user limit stops adding users.

The answer is cached until Fleek's `valid_until`. After that we ask again; if Fleek cannot be reached we
keep using the last good answer for GRACE (7 days), after which the licence reads as `unverified`, which
blocks nothing. A failed attempt is not repeated for RETRY_AFTER, so a down Fleek never slows every
request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Tenant, TenantEntitlement, User
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

log = logging.getLogger(__name__)

GRACE = timedelta(days=7)
RETRY_AFTER = timedelta(minutes=5)
FETCH_TIMEOUT_SECONDS = 5.0

LAPSED = "lapsed"
UNVERIFIED = "unverified"


class EntitlementError(Exception):
    """Fleek could not be asked, or refused to answer."""


@dataclass(frozen=True)
class Licence:
    status: str  # trial | active | lapsed | unlicensed | unverified
    plan_key: str | None = None
    plan_label: str | None = None
    trial_days_left: int | None = None
    users_limit: int | None = None
    campaign_monthly_cap: int | None = None
    stale: bool = False  # the last good answer, used past its valid_until while Fleek is unreachable

    @property
    def lapsed(self) -> bool:
        return self.status == LAPSED

    @property
    def limits_known(self) -> bool:
        return self.status in ("trial", "active", LAPSED) and self.users_limit is not None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def fetch(fleek_org_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Ask Fleek for this organisation's entitlements (signed GET). Raises EntitlementError on any failure."""
    s = get_settings()
    if not s.control_plane_url or not s.control_plane_secret:
        raise EntitlementError("control plane is not configured")
    path = f"/api/internal/metal-erp/orgs/{fleek_org_id}/entitlements"
    stamp = str(int(datetime.now(UTC).timestamp()))
    headers = {
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: sign(s.control_plane_secret, timestamp=stamp, method="GET", path=path, body=b""),
    }
    own = client is None
    http = client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS)
    try:
        resp = http.get(s.control_plane_url.rstrip("/") + path, headers=headers)
    except httpx.HTTPError as exc:
        raise EntitlementError(f"could not reach the control plane: {exc.__class__.__name__}") from exc
    finally:
        if own:
            http.close()
    if resp.status_code != 200:
        raise EntitlementError(f"control plane answered {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise EntitlementError("control plane sent an unreadable answer") from exc
    if not isinstance(body, dict) or "status" not in body or "valid_until" not in body:
        raise EntitlementError("control plane sent an unexpected answer")
    return body


def licence_from_payload(payload: dict[str, Any], *, stale: bool = False) -> Licence:
    """Pure."""
    plan = payload.get("plan") or {}
    limits = payload.get("limits") or {}
    return Licence(
        status=str(payload.get("status") or UNVERIFIED),
        plan_key=plan.get("key"),
        plan_label=plan.get("label"),
        trial_days_left=payload.get("trial_days_left"),
        users_limit=limits.get("users"),
        campaign_monthly_cap=limits.get("campaign_monthly_cap"),
        stale=stale,
    )


def _parse_time(value: Any) -> datetime:
    return _aware(datetime.fromisoformat(str(value)))


def current(
    session: Session,
    tenant: Tenant,
    *,
    now: datetime | None = None,
    fetcher: Callable[[str], dict[str, Any]] = fetch,
) -> Licence | None:
    """The firm's licence, or None for a firm that is not linked to Fleek (unlimited). Never raises: a problem
    reads as `unverified`."""
    if not tenant.fleek_org_id:
        return None
    now = now or datetime.now(UTC)
    row = session.scalar(select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant.id))
    if row is not None and now < _aware(row.valid_until):
        return licence_from_payload(row.payload)
    if row is not None and row.last_attempt_at and now - _aware(row.last_attempt_at) < RETRY_AFTER:
        return _fallback(row, now)
    try:
        payload = fetcher(tenant.fleek_org_id)
        valid_until = _parse_time(payload["valid_until"])
    except (EntitlementError, KeyError, ValueError) as exc:
        log.warning("entitlement refresh failed for firm %s: %s", tenant.id, exc)
        if row is None:
            return Licence(
                status=UNVERIFIED
            )  # never verified and Fleek is not answering: block nothing, ask again soon
        row.last_error = str(exc)[:500]
        row.last_attempt_at = now
        session.flush()
        return _fallback(row, now)
    if row is None:
        row = TenantEntitlement(
            tenant_id=tenant.id, payload=payload, last_verified_at=now, valid_until=valid_until
        )
        session.add(row)
    row.payload = payload
    row.last_verified_at = now
    row.valid_until = valid_until
    row.last_error = None
    row.last_attempt_at = now
    row.status = str(payload.get("status") or UNVERIFIED)[:20]
    session.flush()
    return licence_from_payload(payload)


def _fallback(row: TenantEntitlement, now: datetime) -> Licence:
    """Fleek did not answer: the last good answer for GRACE after its valid_until, then `unverified` (which
    blocks nothing)."""
    if now < _aware(row.valid_until) + GRACE:
        return licence_from_payload(row.payload, stale=True)
    return Licence(status=UNVERIFIED)


# ----------------------------------------------------------------------------- the two rules


def proactive_sends_allowed(session: Session, tenant: Tenant, **kw: Any) -> bool:
    """False only when the licence has lapsed. Unlinked, unverified, trial and active firms may send."""
    licence = current(session, tenant, **kw)
    return licence is None or not licence.lapsed


LAPSED_MESSAGE = "Your Metal ERP licence has lapsed, so reminders are paused. Contact Fleek to renew."


def user_limit_reason(session: Session, tenant: Tenant, **kw: Any) -> str | None:
    """A sentence when adding one more active user would pass the plan's limit, else None (also None when the
    limit is not known)."""
    licence = current(session, tenant, **kw)
    if licence is None or not licence.limits_known:
        return None
    assert licence.users_limit is not None
    active = session.scalar(
        select(func.count()).select_from(User).where(User.tenant_id == tenant.id, User.is_active.is_(True))
    )
    if (active or 0) >= licence.users_limit:
        return (
            f"The {licence.plan_label or 'current'} plan allows {licence.users_limit} users and this "
            "firm already has that many. Change the plan in Fleek to add more."
        )
    return None

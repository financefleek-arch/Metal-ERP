"""Record what a person does after sign-in (docs/PLAN-shop-activity-intelligence.md).

Keys are a fixed list, so the funnel in the Fleek console is exact. Server code records the milestones where the
thing already happens; the browser may send only `BEACON_KEYS`. Counts and keys only: never a name, an amount, a
phone number or free text. Recording never breaks the request it rides on.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ActivityEvent, User

log = logging.getLogger(__name__)

# Server milestones, in funnel order (a shop's stage is the furthest one it has reached).
MILESTONES: tuple[str, ...] = (
    "signed_up",
    "firm_details_saved",
    "party_created",
    "item_created",
    "invoice_draft_created",
    "invoice_finalised",
    "invoice_pdf_out",
    "payment_recorded",
)
# Other server events.
SERVER_KEYS: frozenset[str] = frozenset(
    MILESTONES + ("signed_in", "whatsapp_connected", "feature_requested", "plan_trial_started", "plan_cancelled")
)
# What the browser may report: friction and demand, never content.
BEACON_KEYS: frozenset[str] = frozenset(
    {
        "setup_viewed",
        "setup_step_clicked",
        "invoice_editor_opened",
        "invoice_editor_abandoned",
        "error_shown",
        "plans_viewed",
        "locked_feature_tapped",
        "whatsapp_teaser_tapped",
        "user_limit_hit",
    }
)
KEYS: frozenset[str] = SERVER_KEYS | BEACON_KEYS

SEEN_EVERY = timedelta(minutes=5)
_MAX_PROPS = 4
_MAX_TEXT = 40


def sanitize(props: Any) -> dict[str, str | int | bool] | None:
    """A few short labels, or None. Anything else (nested, long, many) is dropped, so free text cannot leak in."""
    if not isinstance(props, dict):
        return None
    out: dict[str, str | int | bool] = {}
    for k, v in list(props.items())[:_MAX_PROPS]:
        if not isinstance(k, str) or len(k) > 30:
            continue
        if isinstance(v, bool | int) or (isinstance(v, str) and len(v) <= _MAX_TEXT):
            out[k] = v
    return out or None


def record(session: Session, *, tenant_id: str, user_id: str | None, key: str, props: Any = None) -> None:
    if key not in KEYS:
        log.warning("unknown activity key %r ignored", key)
        return
    session.add(ActivityEvent(tenant_id=tenant_id, user_id=user_id, key=key, props=sanitize(props)))


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def touch_seen(user: User, now: datetime | None = None) -> None:
    """Stamp `last_seen_at`, at most every few minutes (so a busy session is one write, not hundreds)."""
    now = now or datetime.now(UTC)
    if user.last_seen_at is None or now - _aware(user.last_seen_at) >= SEEN_EVERY:
        user.last_seen_at = now


def has(session: Session, tenant_id: str, key: str) -> bool:
    return (
        session.scalar(
            select(ActivityEvent.id)
            .where(ActivityEvent.tenant_id == tenant_id, ActivityEvent.key == key)
            .limit(1)
        )
        is not None
    )


def first_times(session: Session, tenant_id: str) -> dict[str, datetime]:
    """key -> when the shop first did it (the milestone times the Fleek funnel is built from)."""
    rows = session.execute(
        select(ActivityEvent.key, func.min(ActivityEvent.created_at))
        .where(ActivityEvent.tenant_id == tenant_id)
        .group_by(ActivityEvent.key)
    ).all()
    return {k: t for k, t in rows}

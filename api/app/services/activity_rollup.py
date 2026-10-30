"""The daily roll-up each shop sends Fleek about what its people do (docs/PLAN-shop-activity-intelligence.md).

One body per shop, full state each time, over the signed courier to `POST /api/internal/metal-erp/orgs/<org_id>/activity`. A repeat or a missed day does
no harm. Counts and keys only: nothing in it is a name, an amount, a phone number or a message.

Shops that were using the app before events were recorded have no events for what they already did. Their milestones are filled from the data they
hold (a first customer, a first invoice) and, because they were using it, they count as having had a PDF out: otherwise every established shop would
look stuck on Fleek's call list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ActivityEvent,
    Invoice,
    Item,
    Party,
    Payment,
    Tenant,
    TenantWhatsappConfig,
    User,
)
from app.models._mixins import InvoiceStatus
from app.services import activity
from app.services.fleek_signed import FleekUnavailable
from app.services.onboarding import firm_details_done
from app.services.fleek_signed import request as fleek_request

log = logging.getLogger(__name__)

VERSION = 1
WEEKS = 8
NUDGE_MIN_GAP = 5 * 60  # a busy shop is sent at most this often by the quick path (the hourly run still covers it)
RESEND_UNCHANGED_AFTER = 23 * 60 * 60  # a shop that did nothing new is still sent about daily, so Fleek's day-count windows stay fresh
_last_sent: dict[str, tuple[str, float]] = {}  # tenant id -> (digest of the body without as_of, when it was sent)
# milestone key -> where an established shop's first time comes from, when no event was ever recorded for it
_BACKFILL = {
    "party_created": (Party, None),
    "item_created": (Item, None),
    "invoice_draft_created": (Invoice, None),
    "invoice_finalised": (Invoice, Invoice.status == InvoiceStatus.final),
    "payment_recorded": (Payment, None),
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def tracking_started(session: Session) -> datetime | None:
    """When the first activity event of any shop was recorded: shops older than this were using the app before it was measured."""
    first = session.scalar(select(func.min(ActivityEvent.created_at)))
    return _aware(first) if first else None


def first_times(session: Session, tenant: Tenant, started: datetime | None) -> dict[str, datetime]:
    first = {k: _aware(v) for k, v in activity.first_times(session, tenant.id).items()}
    first.setdefault("signed_up", _aware(tenant.created_at))
    established = started is None or _aware(tenant.created_at) < started
    for key, (model, extra) in _BACKFILL.items():
        if key in first:
            continue
        stmt = select(func.min(model.created_at)).where(model.tenant_id == tenant.id)
        if extra is not None:
            stmt = stmt.where(extra)
        found = session.scalar(stmt)
        if found:
            first[key] = _aware(found)
    if "firm_details_saved" not in first and firm_details_done(tenant):
        first["firm_details_saved"] = _aware(tenant.created_at)
    if "whatsapp_connected" not in first:
        wa = session.scalar(
            select(func.min(TenantWhatsappConfig.created_at)).where(
                TenantWhatsappConfig.tenant_id == tenant.id, TenantWhatsappConfig.is_active.is_(True)
            )
        )
        if wa:
            first["whatsapp_connected"] = _aware(wa)
    # a shop that was invoicing before events existed is activated by definition; a new shop's PDF must really have left the app
    if "invoice_pdf_out" not in first and established and "invoice_finalised" in first:
        first["invoice_pdf_out"] = first["invoice_finalised"]
    return first


def build(session: Session, tenant: Tenant, now: datetime | None = None, started: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since30 = now - timedelta(days=30)
    first = first_times(session, tenant, started)

    events = session.execute(
        select(ActivityEvent.key, ActivityEvent.created_at).where(
            ActivityEvent.tenant_id == tenant.id, ActivityEvent.created_at >= since30
        )
    ).all()
    counts: dict[str, int] = {}
    days: set[Any] = set()
    weekly = [0] * WEEKS
    for key, at in events:
        at = _aware(at)
        counts[key] = counts.get(key, 0) + 1
        days.add(at.date())
        if key == "invoice_finalised":
            weeks_ago = int((now - at).days // 7)
            if 0 <= weeks_ago < WEEKS:
                weekly[WEEKS - 1 - weeks_ago] += 1
    active7 = len({d for d in days if d >= (now - timedelta(days=7)).date()})

    last_seen = session.scalar(
        select(func.max(User.last_seen_at)).where(User.tenant_id == tenant.id, User.is_platform_admin.is_(False))
    )
    return {
        "version": VERSION,
        "as_of": now.isoformat(),
        "signed_up_at": first["signed_up"].isoformat(),
        "last_seen_at": _aware(last_seen).isoformat() if last_seen else None,
        "active_days_7": active7,
        "active_days_30": len(days),
        "first": {k: v.isoformat() for k, v in first.items()},
        "counts_30d": counts,
        "weekly_invoices": weekly,
    }


def _digest(body: dict[str, Any]) -> str:
    stable = {k: v for k, v in body.items() if k != "as_of"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def _send_one(session: Session, tenant: Tenant, started: datetime | None, *, force: bool = False) -> str:
    """Build and send one shop's roll-up. -> "sent", "skipped" (nothing new since the last send) or "refused" (Fleek said no).
    FleekUnavailable is left to the caller, which stops the run."""
    body = build(session, tenant, started=started)
    digest = _digest(body)
    before = _last_sent.get(tenant.id)
    if not force and before and before[0] == digest and time.time() - before[1] < RESEND_UNCHANGED_AFTER:
        return "skipped"
    code, _ = fleek_request("POST", f"/api/internal/metal-erp/orgs/{tenant.fleek_org_id}/activity", body)
    if code in (200, 204):
        _last_sent[tenant.id] = (digest, time.time())
        return "sent"
    log.warning("activity roll-up for %s refused with %s", tenant.id, code)
    return "refused"


def send_due() -> int:
    """The quick path (every 30 seconds): the shops that just did something (activity.request_send) go to Fleek now. -> how many were sent."""
    due = activity.take_due()
    if not due:
        return 0
    from app.db import SessionLocal

    sent = 0
    with SessionLocal() as session:
        started = tracking_started(session)
        for tenant in session.scalars(select(Tenant).where(Tenant.id.in_(due), Tenant.fleek_org_id.is_not(None))).all():
            try:
                last = _last_sent.get(tenant.id)
                if last and time.time() - last[1] < NUDGE_MIN_GAP:
                    activity.request_send(tenant.id)  # sent a moment ago: look again shortly
                    continue
                if _send_one(session, tenant, started) == "sent":
                    sent += 1
            except FleekUnavailable:
                log.warning("Fleek is not reachable; the hourly run will catch up")
                break
            except Exception:  # noqa: BLE001 - one shop's trouble must not stop the others
                log.exception("activity nudge failed for tenant %s", tenant.id)
    return sent


def send_all() -> int:
    """The scheduler's job (hourly): one roll-up per linked shop whose numbers changed since the last send, and every shop at least daily.
    Own session. A shop's failure never stops the others. -> how many were sent."""
    from app.db import SessionLocal

    sent = 0
    with SessionLocal() as session:
        started = tracking_started(session)
        tenants = session.scalars(select(Tenant).where(Tenant.fleek_org_id.is_not(None))).all()
        for tenant in tenants:
            try:
                if _send_one(session, tenant, started) == "sent":
                    sent += 1
            except FleekUnavailable:
                log.warning("Fleek is not reachable; the rest wait for the next hourly run")
                break
            except Exception:  # noqa: BLE001 - one shop's trouble must not stop the others
                log.exception("activity roll-up failed for tenant %s", tenant.id)
    return sent

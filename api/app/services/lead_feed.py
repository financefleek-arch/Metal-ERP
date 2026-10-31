"""The sign-up lead feed Fleek's admin works from (docs/PLAN-signup-followup-console.md).

A lead is one mobile number (or, for someone who typed an email to sign in, one email) seen on the sign-up or sign-in page in the last LEAD_DAYS days.
One body carries every lead's facts, over the signed courier to `POST /api/internal/metal-erp/leads`; full state each time, so a repeat or a missed
push does no harm. Fleek works out each lead's state and joins the WhatsApp delivery status of the code (only Fleek can: it sent it).

Facts only. Never a code, a code hash, a password, an IP address or a message. The contact itself (the mobile or email) IS sent: Fleek seals it, and it is
what the person following up has to call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuthAttempt, OtpChallenge, SignupRequest, Tenant, User
from app.services.fleek_signed import FleekUnavailable
from app.services.fleek_signed import request as fleek_request

log = logging.getLogger(__name__)

VERSION = 1
LEAD_DAYS = 30
MAX_LEADS = 500
PATH = "/api/internal/metal-erp/leads"
RESEND_UNCHANGED_AFTER = 55 * 60  # an unchanged feed is still sent about hourly, so Fleek's time-based states stay fresh
_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")

_last_sent: dict[str, Any] = {"digest": None, "at": 0.0}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None


def _kind_of(key: str) -> str | None:
    """'mobile' | 'email' | None for anything else a person typed into the sign-in box."""
    if _MOBILE.match(key):
        return "mobile"
    if "@" in key and len(key) <= 200:
        return "email"
    return None


def _new_lead(key: str, kind: str) -> dict[str, Any]:
    return {
        "key": key,
        "kind": kind,
        "contact": key,
        "name": None,
        "firm": None,
        "email": None,
        "signups": [],
        "login_fails": 0,
        "first_login_fail_at": None,
        "last_login_fail_at": None,
        "has_account": False,
        "account_at": None,
        "fleek_org_id": None,
        "ad_click_kind": None,  # first-touch ad click that led to a sign-up attempt (gclid | gbraid | wbraid); optional on the Fleek side
        "ad_click_id": None,
        "attribution": None,  # first-touch facts of the earliest sign-up attempt that has any (services/attribution.py); Fleek works out the source from them
    }


def build(session: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=LEAD_DAYS)
    leads: dict[str, dict[str, Any]] = {}

    requests = session.scalars(
        select(SignupRequest).where(SignupRequest.created_at >= since).order_by(SignupRequest.created_at)
    ).all()
    codes_by_request: dict[str, list[OtpChallenge]] = {}
    if requests:
        for c in session.scalars(
            select(OtpChallenge)
            .where(OtpChallenge.purpose == "signup", OtpChallenge.subject_id.in_([r.id for r in requests]))
            .order_by(OtpChallenge.created_at)
        ):
            codes_by_request.setdefault(c.subject_id, []).append(c)

    for r in requests:
        lead = leads.setdefault(r.mobile, _new_lead(r.mobile, "mobile"))
        lead["name"], lead["firm"] = r.owner_name, r.firm_name  # the latest attempt wins
        if r.email:
            lead["email"] = r.email
        lead["signups"].append(
            {
                "at": _iso(r.created_at),
                "status": r.status,
                "expires_at": _iso(r.expires_at),
                "callback_at": _iso(r.callback_requested_at),
                "codes": [
                    {
                        "channel": c.channel,
                        "sent_at": _iso(c.created_at),
                        "attempts": c.attempts,
                        "used": c.consumed_at is not None,
                    }
                    for c in codes_by_request.get(r.id, [])
                ],
            }
        )
        if r.ad_click_id and not lead["ad_click_id"]:  # requests are in time order: the first click kept
            lead["ad_click_kind"], lead["ad_click_id"] = r.ad_click_kind, r.ad_click_id
        if r.attribution and not lead["attribution"]:
            lead["attribution"] = r.attribution
        if r.tenant_id:
            lead["has_account"] = True

    # a sign-in that failed, counted per person (the `login_email` throttle key is one spelling of the identifier, no IP)
    fails = session.execute(
        select(AuthAttempt.key, func.count(), func.min(AuthAttempt.created_at), func.max(AuthAttempt.created_at))
        .where(AuthAttempt.kind == "login_email", AuthAttempt.created_at >= since)
        .group_by(AuthAttempt.key)
    ).all()
    for key, n, first, last in fails:
        kind = _kind_of(key)
        if kind is None:
            continue
        lead = leads.setdefault(key, _new_lead(key, kind))
        lead["login_fails"] = int(n)
        lead["first_login_fail_at"] = _iso(first)
        lead["last_login_fail_at"] = _iso(last)

    _attach_accounts(session, leads)

    def latest_activity(lead: dict[str, Any]) -> str:
        times = [s["at"] for s in lead["signups"]] + [lead["last_login_fail_at"] or ""]
        return max(times)

    ordered = sorted(leads.values(), key=latest_activity, reverse=True)[:MAX_LEADS]
    return {"version": VERSION, "as_of": now.isoformat(), "leads": ordered}


def _attach_accounts(session: Session, leads: dict[str, dict[str, Any]]) -> None:
    """Mark the leads that already have an account (a mobile or an email in `app_user`), and the Fleek organisation of their firm."""
    if not leads:
        return
    mobiles = [k for k, v in leads.items() if v["kind"] == "mobile"]
    emails = [k for k, v in leads.items() if v["kind"] == "email"]
    rows: list[tuple[User, Tenant]] = []
    if mobiles:
        rows += session.execute(
            select(User, Tenant).join(Tenant, Tenant.id == User.tenant_id).where(User.mobile.in_(mobiles))
        ).all()
    if emails:
        rows += session.execute(
            select(User, Tenant).join(Tenant, Tenant.id == User.tenant_id).where(func.lower(User.email).in_(emails))
        ).all()
    for user, tenant in rows:
        keys = [k for k in (user.mobile, (user.email or "").lower()) if k in leads]
        for k in keys:
            lead = leads[k]
            lead["has_account"] = True
            lead["account_at"] = lead["account_at"] or _iso(user.created_at)
            lead["fleek_org_id"] = lead["fleek_org_id"] or tenant.fleek_org_id


def _digest(body: dict[str, Any]) -> str:
    stable = {k: v for k, v in body.items() if k != "as_of"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def send(force: bool = False) -> int:
    """The scheduler's job: build the feed and send it. Own session. -> how many leads were sent (0 when nothing changed or Fleek is away)."""
    from app.db import SessionLocal

    with SessionLocal() as session:
        body = build(session)
    if not body["leads"]:
        return 0
    digest = _digest(body)
    if not force and digest == _last_sent["digest"] and time.time() - _last_sent["at"] < RESEND_UNCHANGED_AFTER:
        return 0
    try:
        code, _ = fleek_request("POST", PATH, body)
    except FleekUnavailable:
        log.warning("Fleek is not reachable; the lead feed waits for the next run")
        return 0
    if code not in (200, 204):
        log.warning("lead feed refused with %s", code)
        return 0
    _last_sent["digest"], _last_sent["at"] = digest, time.time()
    return len(body["leads"])

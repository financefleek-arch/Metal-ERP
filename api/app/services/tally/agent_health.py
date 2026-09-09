"""Derived health of a firm's companion agent, from its checkin trail.

Two independent signals, both read off `backup_shop`:
- **agent -> Fleek**: `last_checkin_at` within `AGENT_OFFLINE_AFTER` => online.
- **agent -> TallyPrime**: `last_tally_status` ('connected' | 'refused' |
  'no_company' | 'unknown'; None = never reported).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import BackupShop, TallyCompany

# Pilot: dashboard-only, no alerting. Red after 5 minutes of silence
# (checkins are every ~60s).
AGENT_OFFLINE_AFTER = timedelta(minutes=5)

_TALLY_REASON_MESSAGE = {
    "refused": "Tally isn't reachable from the shop's agent — it's likely closed, "
    "or \"acts as Server\" is off. Ask the shop to open TallyPrime with the "
    "company loaded, then retry.",
    "no_company": "The shop's agent can reach Tally, but no company is loaded. "
    "Ask the shop to open the company in TallyPrime, then retry.",
    "unknown": "The shop's agent couldn't confirm it can reach Tally. Retry once "
    "the console shows Tally as connected.",
}


def agent_online(shop: BackupShop, *, now: datetime | None = None) -> bool:
    if shop.last_checkin_at is None:
        return False
    now = now or datetime.now(UTC)
    last = shop.last_checkin_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last) <= AGENT_OFFLINE_AFTER


def tally_reachable_now(shop: BackupShop, *, now: datetime | None = None) -> bool | None:
    """True/False only when we have a *fresh* signal (agent online); None if
    the agent is offline or has never reported — 'we don't know'."""
    if not agent_online(shop, now=now):
        return None
    if shop.last_tally_status is None:
        return None
    return shop.last_tally_status == "connected"


def assert_tally_reachable(session: Session, company: TallyCompany) -> None:
    """A fresh, negative reachability signal blocks an enqueue (pull or
    push) with a message that tells the operator what to ask the shop to
    do — rather than queuing a job that will just sit on 'waiting on
    Tally'. A stale or never-reported signal doesn't block (the agent's own
    retry-on-poll behaviour still applies); only a *current* known-bad
    state does.

    A company with no linked agent (`shop_id is None`) is left to the
    caller's own enqueue function to 422 — that's the more specific error.
    Shared by the operator (`routers/tally.py`) and self-serve
    (`routers/tally_self_serve.py`) push/pull enqueue paths.
    """
    if company.shop_id is None:
        return
    shop = session.get(BackupShop, company.shop_id)
    if shop is None:
        return
    if not agent_online(shop):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The shop's agent hasn't checked in recently — it may be offline. "
            "Confirm the shop's PC is on and connected before trying again.",
        )
    if shop.last_tally_status and shop.last_tally_status != "connected":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_TALLY_REASON_MESSAGE.get(
                shop.last_tally_status, _TALLY_REASON_MESSAGE["unknown"]
            ),
        )

"""Derived health of a firm's companion agent, from its checkin trail.

Two independent signals, both read off `backup_shop`:
- **agent -> Fleek**: `last_checkin_at` within `AGENT_OFFLINE_AFTER` => online.
- **agent -> TallyPrime**: `last_tally_status` ('connected' | 'refused' |
  'no_company' | 'unknown'; None = never reported).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import BackupShop

# Pilot: dashboard-only, no alerting. Red after 5 minutes of silence
# (checkins are every ~60s).
AGENT_OFFLINE_AFTER = timedelta(minutes=5)


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

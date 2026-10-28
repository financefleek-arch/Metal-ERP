"""Sliding-window limits kept in the database (production runs several workers, so a counter in process
memory would let an attacker multiply the limit by the worker count).

`hit` records one attempt and says whether it was still within the limit. It commits, because the
caller usually raises straight after and the request session would roll the record back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import AuthAttempt


def count(session: Session, kind: str, key: str, window_seconds: int) -> int:
    since = datetime.now(UTC) - timedelta(seconds=window_seconds)
    return (
        session.scalar(
            select(func.count())
            .select_from(AuthAttempt)
            .where(AuthAttempt.kind == kind, AuthAttempt.key == key, AuthAttempt.created_at >= since)
        )
        or 0
    )


def hit(session: Session, kind: str, key: str, *, limit: int, window_seconds: int) -> bool:
    """Record one attempt. False when the key was already at its limit (the attempt is still recorded)."""
    allowed = count(session, kind, key, window_seconds) < limit
    session.add(AuthAttempt(kind=kind, key=key[:200]))
    session.commit()
    return allowed


def record(session: Session, kind: str, key: str) -> None:
    """Count one attempt without judging it."""
    session.add(AuthAttempt(kind=kind, key=key[:200]))
    session.commit()


def blocked(session: Session, kind: str, key: str, *, limit: int, window_seconds: int) -> bool:
    """Read only: is the key at its limit."""
    return count(session, kind, key, window_seconds) >= limit


def clear(session: Session, kind: str, key: str) -> None:
    session.execute(delete(AuthAttempt).where(AuthAttempt.kind == kind, AuthAttempt.key == key))
    session.commit()

"""One-time codes: six digits, ten minutes, five tries, one resend a minute, five sends an hour per
target.

Metal ERP makes, hashes and checks the code. Fleek only carries it (WhatsApp `advisor_login_otp_v2`,
or an email), so Fleek keeps no code state. Used for sign-up and for resetting a password.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import OtpChallenge
from app.services import auth_throttle

TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_GAP_SECONDS = 60
SENDS_PER_HOUR = 5


class CodeError(Exception):
    """A refusal the person can act on; str() is the sentence to show."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _digest(code: str) -> str:
    # keyed, so a copy of the table alone does not let anyone brute-force the 1,000,000 codes offline
    return hmac.new(get_settings().jwt_secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def _latest(
    session: Session, purpose: str, subject_id: str, channel: str, *, open_only: bool
) -> OtpChallenge | None:
    q = select(OtpChallenge).where(
        OtpChallenge.purpose == purpose,
        OtpChallenge.subject_id == subject_id,
        OtpChallenge.channel == channel,
    )
    if open_only:
        q = q.where(OtpChallenge.consumed_at.is_(None))
    return session.scalars(q.order_by(OtpChallenge.created_at.desc())).first()


def issue(
    session: Session,
    *,
    purpose: str,
    subject_id: str,
    channel: str,
    target: str,
    deliver: Callable[[str, str], None],
) -> None:
    """Make a fresh code and hand it to `deliver(target, code)`. A new code replaces any older open one.

    Raises CodeError when asked again too soon or too often, or when delivery fails (nothing is kept
    then, so the
    person can ask again straight away)."""
    now = datetime.now(UTC)
    last = _latest(session, purpose, subject_id, channel, open_only=True)
    if last is not None and (now - _aware(last.created_at)).total_seconds() < RESEND_GAP_SECONDS:
        raise CodeError("Please wait a minute before asking for another code.", status=429)
    if not auth_throttle.hit(
        session, f"code_send_{channel}", target, limit=SENDS_PER_HOUR, window_seconds=3600
    ):
        raise CodeError("Too many codes were requested. Try again in an hour.", status=429)

    code = f"{secrets.randbelow(1_000_000):06d}"
    session.execute(
        delete(OtpChallenge).where(
            OtpChallenge.purpose == purpose,
            OtpChallenge.subject_id == subject_id,
            OtpChallenge.channel == channel,
            OtpChallenge.consumed_at.is_(None),
        )
    )
    row = OtpChallenge(
        purpose=purpose,
        subject_id=subject_id,
        channel=channel,
        target=target,
        code_hash=_digest(code),
        expires_at=now + TTL,
    )
    session.add(row)
    session.commit()
    try:
        deliver(target, code)
    except Exception:
        session.delete(row)
        session.commit()
        raise


def check(session: Session, *, purpose: str, subject_id: str, channel: str, code: str) -> OtpChallenge | None:
    """The open challenge when `code` is right, else None. A wrong code counts against the challenge, which is
    burned after MAX_ATTEMPTS. Does NOT consume: call `consume` once everything else has succeeded."""
    row = _latest(session, purpose, subject_id, channel, open_only=True)
    if row is None or _aware(row.expires_at) <= datetime.now(UTC) or row.attempts >= MAX_ATTEMPTS:
        return None
    if hmac.compare_digest(row.code_hash, _digest(code.strip())):
        return row
    row.attempts += 1
    session.commit()
    return None


def consume(session: Session, *rows: OtpChallenge) -> None:
    now = datetime.now(UTC)
    for r in rows:
        r.consumed_at = now

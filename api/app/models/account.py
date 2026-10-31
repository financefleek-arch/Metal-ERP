"""Sign-up, one-time codes and sign-in throttling (docs/PLAN-onboarding-and-account.md).

A sign-up is a pending row that holds no firm: the firm and its owner exist only after the owner
proves they control the mobile and the email. One-time codes and failed-attempt counts live in the
database, not in process memory, because production runs several workers.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class SignupRequest(PkUuidMixin, TimestampMixin, Base):
    """What the person typed, waiting for the WhatsApp code. The password is already hashed."""

    __tablename__ = "signup_request"

    firm_name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), index=True)  # optional, never verified
    mobile: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # +91XXXXXXXXXX
    password_hash: Mapped[str | None] = mapped_column(String(255))  # None: the password is chosen after the first sign-in
    terms_version: Mapped[str] = mapped_column(String(20), nullable=False)
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # pending | done | cancelled
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    # Google Ads click this sign-up came from (gclid | gbraid | wbraid), kept so a real sign-up can be sent back to Google.
    ad_click_kind: Mapped[str | None] = mapped_column(String(8))
    ad_click_id: Mapped[str | None] = mapped_column(String(200), index=True)
    # First-touch facts the page sent (utm tags, referrer host, landing, device) plus os / browser / in-app from the User-Agent; see services/attribution.py.
    attribution: Mapped[dict | None] = mapped_column(_JSON)
    # Filled when the firm has been made, so a repeated verify answers with the same firm.
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenant.id"))
    fleek_org_id: Mapped[str | None] = mapped_column(String(64))
    # "Ask us to call me" on the code screen; Fleek's Follow-ups queue shows it.
    callback_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OtpChallenge(PkUuidMixin, Base):
    """One code sent to one target for one purpose. Only the hash is stored."""

    __tablename__ = "otp_challenge"
    __table_args__ = (Index("ix_otp_subject", "purpose", "subject_id", "channel"),)

    purpose: Mapped[str] = mapped_column(String(12), nullable=False)  # signup | reset
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)  # signup_request.id or app_user.id
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # whatsapp | email
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthAttempt(PkUuidMixin, Base):
    """One counted hit against a limit (a sign-in try, a code send). Counted over a sliding window."""

    __tablename__ = "auth_attempt"
    __table_args__ = (Index("ix_auth_attempt_lookup", "kind", "key", "created_at"),)

    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

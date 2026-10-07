"""Payment reminders: proposed each day, sent only when the shop approves them."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class PaymentReminder(PkUuidMixin, TimestampMixin, Base):
    """One proposal for one party: a `payment_reminder` for a single overdue invoice, or an
    `account_statement` when several are overdue. `dedupe_key` makes the daily run idempotent."""

    __tablename__ = "payment_reminder"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_payment_reminder_dedupe"),
        Index("ix_payment_reminder_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # invoice | statement
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoice.id", ondelete="SET NULL"))
    invoice_ids: Mapped[list | None] = mapped_column(JSON)
    stage_days: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    oldest_overdue_days: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="proposed", nullable=False)
    error: Mapped[str | None] = mapped_column(String(1000))
    decided_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(36))
    dedupe_key: Mapped[str] = mapped_column(String(80), nullable=False)

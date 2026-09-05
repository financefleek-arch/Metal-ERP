"""Payment (party ledger, Tally bill-wise-allocation style) and its
allocations against open invoices / on-account credit.

A payment is created posted (no draft/finalize split). Reversal (cheque
bounce / wrong entry) is a pure status flip + timestamp — allocation rows
are never deleted, so every balance query filters `payment.status ==
posted` and a reversed payment stops counting automatically without
losing the audit trail.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import (
    AllocationType,
    PaymentMode,
    PaymentStatus,
    PkUuidMixin,
    TimestampMixin,
)

if TYPE_CHECKING:
    from app.models.invoice import Invoice

_MONEY = Numeric(15, 2)


class Payment(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "payment"
    __table_args__ = (
        UniqueConstraint("tenant_id", "voucher_no", name="uq_payment_tenant_voucher_no"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), nullable=False, index=True)

    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    mode: Mapped[PaymentMode] = mapped_column(String(10), nullable=False)
    ref_no: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    # gap-free per-tenant sequence, assigned atomically at creation
    # (mirrors invoice.number via the same number_sequence mechanism).
    voucher_no: Mapped[int | None] = mapped_column(Integer)

    # Scaffolding for a future Tally export — not read by any app logic yet.
    # Defaults from `mode` (Cash / Bank) at creation, freeform override allowed.
    ledger_name: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[PaymentStatus] = mapped_column(
        String(10), default=PaymentStatus.posted, nullable=False, index=True
    )
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_reason: Mapped[str | None] = mapped_column(Text)

    allocations: Mapped[list[PaymentAllocation]] = relationship(
        back_populates="payment",
        # payments are never hard-deleted, only reversed — no delete-orphan
        cascade="save-update, merge",
        order_by="PaymentAllocation.created_at",
    )


class PaymentAllocation(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "payment_allocation"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
        # The invoice_id-null-iff-on_account invariant is NOT enforced here as
        # a multi-column CHECK — SQLAlchemy/Alembic CHECK constraints spanning
        # columns are awkward to keep portable across the SQLite (tests) and
        # Postgres (prod) dialects this project supports side by side. It is
        # enforced in the service/router layer instead (see
        # app/services/payments.py and app/routers/payments.py), which is
        # also where the tenant/party ownership checks already have to live.
    )

    payment_id: Mapped[str] = mapped_column(ForeignKey("payment.id"), nullable=False, index=True)
    # ON DELETE SET NULL (migration 0019): a payment_allocation row is never
    # deleted (audit trail — see payment.py docstring / migration 0017), but
    # its invoice CAN be deleted once cancelled and unpaid (posted payments
    # block that at the app layer). A reversed payment's allocation must not
    # permanently pin its invoice, so losing invoice_id here — not the row —
    # is how that delete is allowed to go through.
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[AllocationType] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="allocations")
    invoice: Mapped[Invoice | None] = relationship(viewonly=True)

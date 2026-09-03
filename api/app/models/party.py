"""Party (customer / supplier / both) and its addresses."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import (
    AddressType,
    PartyRole,
    PartySource,
    PartyStatus,
    PkUuidMixin,
    TimestampMixin,
)


class Party(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "party"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    pan: Mapped[str | None] = mapped_column(String(10))
    role: Mapped[PartyRole] = mapped_column(String(10), default=PartyRole.customer, nullable=False)
    default_state_code: Mapped[str | None] = mapped_column(String(2))

    # Opt-in for WhatsApp messages (invoice-ready, payment reminders). Off by
    # default; a send is refused unless this is true AND `phone` is set.
    whatsapp_optin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Lifecycle: archived parties drop out of the default list and every picker,
    # but stay linked to the documents that already reference them.
    status: Mapped[PartyStatus] = mapped_column(
        String(10), default=PartyStatus.active, nullable=False, index=True
    )

    # Provenance: how this row came to exist. Set once at creation; display-only.
    source: Mapped[PartySource] = mapped_column(
        String(20), default=PartySource.manual, nullable=False
    )
    # For source=inward_bill this is the inward_bill.id; deep-links back to it.
    source_ref: Mapped[str | None] = mapped_column(String(36))

    # Last transaction date (invoice finalize / inward approve). Forward-only.
    # Null = never billed. Powers the dormancy filter.
    last_txn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Phase 2 — dormant.
    gstin: Mapped[str | None] = mapped_column(String(15))

    # Tally import back-reference for idempotent refresh.
    tally_guid: Mapped[str | None] = mapped_column(String(64), index=True)

    addresses: Mapped[list[PartyAddress]] = relationship(
        back_populates="party", cascade="all, delete-orphan"
    )


class PartyAddress(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "party_address"

    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), nullable=False, index=True)
    type: Mapped[AddressType] = mapped_column(String(10), default=AddressType.both, nullable=False)
    line1: Mapped[str | None] = mapped_column(String(200))
    line2: Mapped[str | None] = mapped_column(String(200))
    line3: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))
    pincode: Mapped[str | None] = mapped_column(String(6))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    party: Mapped[Party] = relationship(back_populates="addresses")

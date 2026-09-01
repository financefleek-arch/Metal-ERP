"""Invoice and its lines.

Totals columns are frozen at finalize (written once, from the pure
`domain.tax.compute_invoice` result) — never recomputed on read, so a
finalized invoice is immutable in practice. Phase-2 (GST) and Stage-2+
(weighment / stock lot) columns exist now, nullable and unused.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
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
    DocType,
    InvoiceStatus,
    PdfStatus,
    PkUuidMixin,
    TimestampMixin,
)

if TYPE_CHECKING:
    from app.models.item import Item
    from app.models.party import Party, PartyAddress

_MONEY = Numeric(15, 2)


class Invoice(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "invoice"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "series", "fy", "number", name="uq_invoice_tenant_series_fy_number"
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    doc_type: Mapped[DocType] = mapped_column(String(4), default=DocType.inv, nullable=False)

    series: Mapped[str] = mapped_column(String(20), default="Sales", nullable=False)
    number: Mapped[int | None] = mapped_column(Integer)  # assigned on finalize
    fy: Mapped[str] = mapped_column(String(9), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), nullable=False, index=True)
    bill_to_addr_id: Mapped[str | None] = mapped_column(ForeignKey("party_address.id"))
    ship_to_addr_id: Mapped[str | None] = mapped_column(ForeignKey("party_address.id"))

    notes: Mapped[str | None] = mapped_column(Text)
    terms_snapshot: Mapped[str | None] = mapped_column(Text)
    declaration_snapshot: Mapped[str | None] = mapped_column(Text)

    # Editor input: an absolute amount off the whole bill. `domain.tax`
    # applies it after subtotal, before round-off; `discount_total` (below)
    # is the frozen line+invoice sum written at finalize.
    invoice_discount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)

    status: Mapped[InvoiceStatus] = mapped_column(
        String(10), default=InvoiceStatus.draft, nullable=False, index=True
    )
    template_version: Mapped[str] = mapped_column(
        String(20), default="v1-nongst", nullable=False
    )
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    pdf_status: Mapped[PdfStatus] = mapped_column(
        String(10), default=PdfStatus.none, nullable=False
    )

    # --- frozen at finalize ---
    subtotal: Mapped[Decimal | None] = mapped_column(_MONEY)
    discount_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    round_off: Mapped[Decimal | None] = mapped_column(_MONEY)
    grand_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    amount_in_words: Mapped[str | None] = mapped_column(String(500))

    # --- Phase 2 (GST) — dormant ---
    place_of_supply_state_code: Mapped[str | None] = mapped_column(String(2))
    supply_type: Mapped[str | None] = mapped_column(String(20))
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    taxable_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    cgst_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    sgst_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    igst_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    cess_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    tax_in_words: Mapped[str | None] = mapped_column(String(500))
    irn: Mapped[str | None] = mapped_column(String(100))
    ack_no: Mapped[str | None] = mapped_column(String(50))
    ack_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_qr: Mapped[str | None] = mapped_column(Text)
    signed_invoice: Mapped[str | None] = mapped_column(Text)
    ewb_no: Mapped[str | None] = mapped_column(String(20))
    ewb_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ewb_valid_till: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_km: Mapped[int | None] = mapped_column(Integer)
    transport_mode: Mapped[str | None] = mapped_column(String(20))
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    transporter_id: Mapped[str | None] = mapped_column(String(20))
    gstn_status: Mapped[str | None] = mapped_column(String(20))

    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.sl_no",
    )
    party: Mapped[Party] = relationship(lazy="joined", viewonly=True)
    bill_to_addr: Mapped[PartyAddress | None] = relationship(
        foreign_keys=[bill_to_addr_id], viewonly=True
    )
    ship_to_addr: Mapped[PartyAddress | None] = relationship(
        foreign_keys=[ship_to_addr_id], viewonly=True
    )


class InvoiceLine(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "invoice_line"

    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.id"), nullable=False, index=True)
    sl_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)

    description: Mapped[str] = mapped_column(String(300), nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(ForeignKey("hsn_code.code"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    uom: Mapped[str | None] = mapped_column(String(20))
    unit_rate: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    discount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    line_total: Mapped[Decimal | None] = mapped_column(_MONEY)

    # --- Phase 2 (GST) — dormant ---
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    is_rate_inclusive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    taxable_value: Mapped[Decimal | None] = mapped_column(_MONEY)
    cgst_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    sgst_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    igst_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    cess_amt: Mapped[Decimal | None] = mapped_column(_MONEY)

    # --- Stage 2+ — dormant ---
    weighment_id: Mapped[str | None] = mapped_column(String(36))
    stock_lot_id: Mapped[str | None] = mapped_column(String(36))
    size_pos: Mapped[int | None] = mapped_column(Integer)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    item: Mapped[Item | None] = relationship(lazy="joined", viewonly=True)

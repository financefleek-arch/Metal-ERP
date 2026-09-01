"""Inward Bill Import — supplier PDF invoice → Tally Purchase-voucher XML.

A pluggable, feature-flagged module (`tenant.ext_inward_import`). All tables
are tenant-scoped and follow the existing mixin/enum style. Money is
NUMERIC(15,2); confidence NUMERIC(4,3); JSON columns use the `_JSON` variant
(JSONB on Postgres, plain JSON on SQLite for tests).

Schema per docs/EXTENSION-inward-bill-import.md → *Schema*.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import (
    ExtractionMethod,
    InwardStatus,
    JobStatus,
    MatchMethod,
    PkUuidMixin,
    SupplyType,
    TimestampMixin,
)

# JSONB on Postgres (indexable, typed), plain JSON on SQLite (tests/CI).
_JSON = JSON().with_variant(JSONB(), "postgresql")

_MONEY = Numeric(15, 2)
_QTY = Numeric(15, 3)
_RATE = Numeric(5, 2)
_CONF = Numeric(4, 3)


class InwardBill(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "inward_bill"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))

    # Source file — same bind-mounted volume as invoice PDFs, `inward/` subdir.
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_pdf_path: Mapped[str | None] = mapped_column(String(500))

    # Supplier (extracted, then resolved)
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    supplier_gstin: Mapped[str | None] = mapped_column(String(15))
    supplier_pan: Mapped[str | None] = mapped_column(String(10))
    supplier_state_code: Mapped[str | None] = mapped_column(String(2))
    # Set on resolve; null = a new supplier party is staged in the JSON below.
    matched_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"), index=True)
    new_supplier_staged_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)

    # Document
    bill_no: Mapped[str | None] = mapped_column(String(50))
    bill_date: Mapped[date | None] = mapped_column(Date)
    sales_order_ref: Mapped[str | None] = mapped_column(String(50))
    place_of_supply_state_code: Mapped[str | None] = mapped_column(String(2))
    supply_type: Mapped[SupplyType | None] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    # Totals
    taxable_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    cgst_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    sgst_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    igst_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    cess_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    round_off: Mapped[Decimal | None] = mapped_column(_MONEY)
    grand_total: Mapped[Decimal | None] = mapped_column(_MONEY)
    amount_in_words: Mapped[str | None] = mapped_column(String(500))

    # Extraction
    extraction_method: Mapped[ExtractionMethod | None] = mapped_column(String(20))
    extraction_confidence: Mapped[Decimal | None] = mapped_column(_CONF)
    reconciled: Mapped[bool | None] = mapped_column(Boolean)
    reconcile_discrepancy: Mapped[Decimal | None] = mapped_column(_MONEY)

    status: Mapped[InwardStatus] = mapped_column(
        String(20), default=InwardStatus.uploaded, nullable=False, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    reject_reason: Mapped[str | None] = mapped_column(Text)

    tally_xml_path: Mapped[str | None] = mapped_column(String(500))
    raw_text: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[InwardBillLine]] = relationship(
        back_populates="bill", cascade="all, delete-orphan", order_by="InwardBillLine.sl_no"
    )
    runs: Mapped[list[ExtractionRun]] = relationship(
        back_populates="bill", cascade="all, delete-orphan", order_by="ExtractionRun.attempt"
    )


class InwardBillLine(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "inward_bill_line"

    inward_bill_id: Mapped[str] = mapped_column(
        ForeignKey("inward_bill.id"), nullable=False, index=True
    )
    sl_no: Mapped[int] = mapped_column(Integer, nullable=False)

    description: Mapped[str] = mapped_column(String(300), nullable=False)
    hsn: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[Decimal | None] = mapped_column(_QTY)
    uom: Mapped[str | None] = mapped_column(String(20))
    unit_rate: Mapped[Decimal | None] = mapped_column(_MONEY)
    discount_pct: Mapped[Decimal | None] = mapped_column(_RATE)
    discount_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    taxable_value: Mapped[Decimal | None] = mapped_column(_MONEY)
    cgst_rate: Mapped[Decimal | None] = mapped_column(_RATE)
    cgst_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    sgst_rate: Mapped[Decimal | None] = mapped_column(_RATE)
    sgst_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    igst_rate: Mapped[Decimal | None] = mapped_column(_RATE)
    igst_amt: Mapped[Decimal | None] = mapped_column(_MONEY)
    line_total: Mapped[Decimal | None] = mapped_column(_MONEY)

    # Resolution
    match_method: Mapped[MatchMethod | None] = mapped_column(String(10))
    match_confidence: Mapped[Decimal | None] = mapped_column(_CONF)
    matched_item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)
    new_item_staged_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    # 'unknown_hsn' | 'low_confidence' | 'ambiguous' | 'new'
    review_flag: Mapped[str | None] = mapped_column(String(20))

    bill: Mapped[InwardBill] = relationship(back_populates="lines")


class SupplierTemplate(PkUuidMixin, TimestampMixin, Base):
    """Saved from an approved bill; applied to the tenant's next bill from the
    same GSTIN to skip the fuzzy ladder + LLM. X6.
    """

    __tablename__ = "supplier_template"
    __table_args__ = ()

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    supplier_gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(String(200))

    column_ranges_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    header_anchors_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    uom_map_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)

    default_purchase_ledger: Mapped[str | None] = mapped_column(String(100))
    default_cgst_ledger: Mapped[str | None] = mapped_column(String(100))
    default_sgst_ledger: Mapped[str | None] = mapped_column(String(100))
    default_igst_ledger: Mapped[str | None] = mapped_column(String(100))

    created_from_bill_id: Mapped[str | None] = mapped_column(ForeignKey("inward_bill.id"))


class TallyLedgerConfig(Base):
    """One row per tenant. Ledger names must match the shop's Tally chart of
    accounts or the import throws <LINEERROR>. Auto-created with GST-standard
    defaults on first read.
    """

    __tablename__ = "tally_ledger_config"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    creditors_group: Mapped[str] = mapped_column(
        String(100), default="Sundry Creditors", nullable=False
    )
    purchase_ledger: Mapped[str] = mapped_column(
        String(100), default="Purchase Accounts", nullable=False
    )
    cgst_ledger: Mapped[str] = mapped_column(String(100), default="CGST", nullable=False)
    sgst_ledger: Mapped[str] = mapped_column(String(100), default="SGST", nullable=False)
    igst_ledger: Mapped[str] = mapped_column(String(100), default="IGST", nullable=False)
    round_off_ledger: Mapped[str] = mapped_column(
        String(100), default="Round Off", nullable=False
    )
    xml_encoding: Mapped[str] = mapped_column(String(10), default="UTF-16", nullable=False)


class ExtractionRun(PkUuidMixin, TimestampMixin, Base):
    """Audit / retry log — one row per extraction attempt on a bill."""

    __tablename__ = "extraction_run"

    inward_bill_id: Mapped[str] = mapped_column(
        ForeignKey("inward_bill.id"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str | None] = mapped_column(String(20))
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(_CONF)
    error: Mapped[str | None] = mapped_column(Text)
    llm_tokens: Mapped[int | None] = mapped_column(Integer)

    bill: Mapped[InwardBill] = relationship(back_populates="runs")


class Job(PkUuidMixin, Base):
    """A minimal Postgres-row work queue (no Redis). Only the batch-extraction
    path (X7) enqueues; X1–X6 run extraction synchronously. Registered now so
    one migration covers it.
    """

    __tablename__ = "job"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    status: Mapped[JobStatus] = mapped_column(
        String(10), default=JobStatus.queued, nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

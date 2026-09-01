"""Pydantic models for the invoice API.

A draft is edited as a whole: `PUT /api/invoices/{id}` replaces the header
plus the entire line list (last-write-wins, single-editor assumed for M1).
Totals on a draft are computed on read via `domain.tax` and returned but
not persisted; on a finalized invoice they are the frozen DB columns.
"""

from __future__ import annotations

from datetime import date as date_t
from datetime import datetime as datetime_t
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models._mixins import InvoiceStatus, PdfStatus

Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
Qty = Annotated[Decimal, Field(max_digits=15, decimal_places=3)]

_DESC = Field(min_length=1, max_length=300)


# --------------------------------------------------------------------------
# lines
# --------------------------------------------------------------------------


class InvoiceLineIn(BaseModel):
    """One editor row. `item_id` is set only once the typist picks a match;
    a free-typed description with no match stays text and the item is
    created at finalize.
    """

    item_id: str | None = None
    group_id: str | None = None  # set when the row resolved to a group + size
    description: str = _DESC
    hsn_code: str | None = Field(default=None, max_length=8)
    quantity: Qty = Decimal("0")
    uom: str | None = Field(default=None, max_length=20)
    unit_rate: Money = Decimal("0")
    discount: Money = Decimal("0")  # absolute amount off this line
    size_pos: int | None = None


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sl_no: int
    item_id: str | None
    description: str
    hsn_code: str | None
    quantity: Qty
    uom: str | None
    unit_rate: Money
    discount: Money
    line_total: Money | None


# --------------------------------------------------------------------------
# header / create / update
# --------------------------------------------------------------------------


class InvoiceCreate(BaseModel):
    party_id: str
    date: date_t | None = None  # defaults to today
    bill_to_addr_id: str | None = None
    ship_to_addr_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    invoice_discount: Money = Decimal("0")
    lines: list[InvoiceLineIn] = Field(default_factory=list)


class InvoiceUpdate(BaseModel):
    """Full replace of the editable surface. Every field optional so a
    partial PUT is tolerated, but `lines`, when given, replaces all rows.
    """

    party_id: str | None = None
    date: date_t | None = None
    bill_to_addr_id: str | None = None
    ship_to_addr_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    invoice_discount: Money | None = None
    lines: list[InvoiceLineIn] | None = None


# --------------------------------------------------------------------------
# read models
# --------------------------------------------------------------------------


class InvoiceTotals(BaseModel):
    subtotal: Money
    discount_total: Money
    taxable_total: Money
    round_off: Money
    grand_total: Money
    amount_in_words: str


class PartyBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    gstin: str | None = None
    pan: str | None = None
    default_state_code: str | None = None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    doc_type: str
    series: str
    number: int | None
    fy: str
    date: date_t
    status: InvoiceStatus
    template_version: str

    party_id: str
    party: PartyBrief | None = None
    bill_to_addr_id: str | None
    ship_to_addr_id: str | None

    notes: str | None
    terms_snapshot: str | None
    declaration_snapshot: str | None

    invoice_discount: Money
    # computed-on-read for a draft; frozen columns for a finalized invoice
    totals: InvoiceTotals

    pdf_status: PdfStatus
    has_pdf: bool = False

    lines: list[InvoiceLineOut] = Field(default_factory=list)
    finalize_blockers: list[str] = Field(default_factory=list)

    created_at: datetime_t
    updated_at: datetime_t


class InvoiceListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: int | None
    fy: str
    date: date_t
    status: InvoiceStatus
    party_id: str
    party_name: str
    grand_total: Money | None
    pdf_status: PdfStatus


class FinalizeOut(BaseModel):
    id: str
    number: int
    fy: str
    status: InvoiceStatus
    totals: InvoiceTotals
    pdf_status: PdfStatus
    created_item_ids: list[str] = Field(default_factory=list)
    learned_group_ids: list[str] = Field(default_factory=list)


class DuplicateOut(BaseModel):
    id: str

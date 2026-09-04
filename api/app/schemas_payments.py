"""Pydantic models for the Payments API — party-ledger payment recording
and allocation against open invoices (Tally bill-wise-allocation style).
"""

from __future__ import annotations

from datetime import date as date_t
from datetime import datetime as datetime_t
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models._mixins import AllocationType, PaymentMode, PaymentStatus

Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


class PaymentAllocationIn(BaseModel):
    invoice_id: str | None = None
    type: AllocationType
    amount: Money = Field(gt=0)


class PaymentCreate(BaseModel):
    party_id: str
    date: date_t | None = None  # defaults to today
    amount: Money = Field(gt=0)
    mode: PaymentMode
    ref_no: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    ledger_name: str | None = Field(default=None, max_length=100)
    # client sends only the allocations it knows about; if their sum is less
    # than `amount` the server auto-creates the on_account remainder — the
    # client never has to compute or send it.
    allocations: list[PaymentAllocationIn] = Field(default_factory=list)


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------


class PaymentAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_id: str | None
    invoice_number: int | None = None
    type: AllocationType
    amount: Money


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    party_id: str
    party_name: str | None = None
    date: date_t
    amount: Money
    mode: PaymentMode
    ref_no: str | None
    notes: str | None
    voucher_no: int | None
    ledger_name: str | None
    status: PaymentStatus
    reversed_at: datetime_t | None
    reversed_reason: str | None
    allocations: list[PaymentAllocationOut] = Field(default_factory=list)
    created_at: datetime_t
    updated_at: datetime_t


class PaymentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    party_id: str
    party_name: str | None = None
    date: date_t
    amount: Money
    mode: PaymentMode
    voucher_no: int | None
    status: PaymentStatus


class ReversePaymentIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------
# open invoices (payment dialog allocation table)
# --------------------------------------------------------------------------


class OpenInvoiceForAllocation(BaseModel):
    invoice_id: str
    number: int | None
    date: date_t
    grand_total: Money
    balance_due: Money
    days_old: int


# --------------------------------------------------------------------------
# party ledger statement
# --------------------------------------------------------------------------


class PartyLedgerEntry(BaseModel):
    kind: Literal["invoice", "payment"]
    date: date_t
    ref_id: str
    ref_label: str
    debit: Money
    credit: Money
    running_balance: Money
    status: str
    allocations: list[dict] | None = None


# --------------------------------------------------------------------------
# collections
# --------------------------------------------------------------------------


class CollectionsRow(BaseModel):
    party_id: str
    legal_name: str
    phone: str | None
    outstanding_balance: Money
    oldest_unpaid_days: int | None
    open_invoice_count: int

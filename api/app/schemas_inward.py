"""Pydantic request/response models for the Inward Bill Import API.

Kept separate from schemas.py — this whole surface is behind ext_inward_import.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models._mixins import (
    ExtractionMethod,
    InwardStatus,
    MatchMethod,
    SupplyType,
)

# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


class InwardBillListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_filename: str
    supplier_name: str | None
    supplier_gstin: str | None
    bill_no: str | None
    bill_date: date | None
    grand_total: Decimal | None
    status: InwardStatus
    reconciled: bool | None
    extraction_method: ExtractionMethod | None
    extraction_confidence: Decimal | None
    created_at: datetime


# --------------------------------------------------------------------------
# detail
# --------------------------------------------------------------------------


class InwardLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sl_no: int
    description: str
    hsn: str | None
    quantity: Decimal | None
    uom: str | None
    unit_rate: Decimal | None
    discount_pct: Decimal | None
    taxable_value: Decimal | None
    cgst_rate: Decimal | None
    cgst_amt: Decimal | None
    sgst_rate: Decimal | None
    sgst_amt: Decimal | None
    igst_rate: Decimal | None
    igst_amt: Decimal | None
    line_total: Decimal | None
    match_method: MatchMethod | None
    match_confidence: Decimal | None
    matched_item_id: str | None
    new_item_staged_json: dict[str, Any] | None
    review_flag: str | None


class ReconciliationOut(BaseModel):
    reconciled: bool | None
    discrepancy: Decimal | None
    taxable_total: Decimal | None
    cgst_total: Decimal | None
    sgst_total: Decimal | None
    igst_total: Decimal | None
    round_off: Decimal | None
    grand_total: Decimal | None


class SupplierOut(BaseModel):
    matched_party_id: str | None
    matched_party_name: str | None
    staged: dict[str, Any] | None
    supply_type: SupplyType | None
    place_of_supply_state_code: str | None


class InwardBillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_filename: str
    status: InwardStatus
    bill_no: str | None
    bill_date: date | None
    sales_order_ref: str | None
    amount_in_words: str | None
    extraction_method: ExtractionMethod | None
    extraction_confidence: Decimal | None
    error_message: str | None
    reject_reason: str | None
    tally_xml_path: str | None
    created_at: datetime

    supplier: SupplierOut
    reconciliation: ReconciliationOut
    lines: list[InwardLineOut]
    approve_blockers: list[str]


# --------------------------------------------------------------------------
# patch (reviewer edits)
# --------------------------------------------------------------------------


class InwardLinePatch(BaseModel):
    sl_no: int
    matched_item_id: str | None = None  # set -> match_method becomes 'manual'
    clear_match: bool = False  # unset the match, back to staged-new
    review_flag: str | None = None


class InwardBillPatch(BaseModel):
    bill_no: str | None = None
    bill_date: date | None = None
    place_of_supply_state_code: str | None = None
    supplier_matched_party_id: str | None = None  # link to an existing party
    use_staged_supplier: bool = False  # switch back to the staged new party
    lines: list[InwardLinePatch] | None = None


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


# --------------------------------------------------------------------------
# approve result
# --------------------------------------------------------------------------


class ApproveOut(BaseModel):
    status: InwardStatus
    created_supplier_id: str | None
    promoted_party_id: str | None
    created_item_ids: list[str]
    linked_line_count: int
    xml_download_url: str


# --------------------------------------------------------------------------
# ledger settings
# --------------------------------------------------------------------------


class LedgerConfigIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    creditors_group: str = "Sundry Creditors"
    purchase_ledger: str = "Purchase Accounts"
    cgst_ledger: str = "CGST"
    sgst_ledger: str = "SGST"
    igst_ledger: str = "IGST"
    round_off_ledger: str = "Round Off"
    xml_encoding: str = "UTF-16"

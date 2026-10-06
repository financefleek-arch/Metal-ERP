"""Supplier documents: one front door for bills and price lists."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep
from app.models import InwardBill, Tenant
from app.services import document_kind

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_BYTES = 60 * 1024 * 1024


class DetectOut(BaseModel):
    kind: str  # bill | price_list | unknown
    reasons: list[str]
    pages: int
    # what this firm can use: a flag that is off removes that choice
    bills_enabled: bool
    price_lists_enabled: bool


@router.post("/detect", response_model=DetectOut)
def detect(
    file: Annotated[UploadFile, File()], user: CurrentUser, session: SessionDep
) -> DetectOut:
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    if not (tenant.ext_inward_import or tenant.ext_supplier_catalog):
        raise HTTPException(status_code=404, detail="Not found")
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is over 60 MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Upload a PDF file.")
    g = document_kind.guess(data)
    kind = g.kind
    # never suggest something this firm cannot do
    if kind == "bill" and not tenant.ext_inward_import:
        kind = "price_list"
    if kind == "price_list" and not tenant.ext_supplier_catalog:
        kind = "bill"
    if kind == "unknown":
        kind = "bill" if tenant.ext_inward_import else "price_list"
        g.reasons.append("Defaulting; change it if that is wrong.")
    return DetectOut(
        kind=kind,
        reasons=g.reasons,
        pages=g.pages,
        bills_enabled=bool(tenant.ext_inward_import),
        price_lists_enabled=bool(tenant.ext_supplier_catalog),
    )


class SupplierBill(BaseModel):
    id: str
    bill_no: str | None
    bill_date: date | None
    grand_total: Decimal | None
    status: str


class SupplierBills(BaseModel):
    bills: list[SupplierBill]
    # what approved bills add up to
    spend: Decimal


@router.get("/by-supplier/{party_id}", response_model=SupplierBills)
def bills_of_supplier(party_id: str, user: CurrentUser, session: SessionDep) -> SupplierBills:
    """This supplier's bills and what you have spent with them (approved bills only)."""
    tenant = session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.ext_inward_import:
        raise HTTPException(status_code=404, detail="Not found")
    rows = list(
        session.scalars(
            select(InwardBill)
            .where(InwardBill.tenant_id == user.tenant_id, InwardBill.matched_party_id == party_id)
            .order_by(InwardBill.created_at.desc())
            .limit(100)
        )
    )
    spend = sum(
        (Decimal(str(b.grand_total)) for b in rows if b.status == "approved" and b.grand_total),
        Decimal("0"),
    )
    return SupplierBills(
        bills=[
            SupplierBill(
                id=b.id,
                bill_no=b.bill_no,
                bill_date=b.bill_date,
                grand_total=b.grand_total,
                status=str(b.status),
            )
            for b in rows
        ],
        spend=spend,
    )

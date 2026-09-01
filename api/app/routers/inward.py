"""Inward Bill Import API — /api/inward-bills.

The whole router is gated by `require_inward`: a tenant without
`ext_inward_import` gets a 404 on every route here (module is invisible, not
just hidden). Write actions need owner/accountant; viewer can list + read.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from app.config import get_settings
from app.deps import SessionDep, get_current_user, require_write
from app.models import InwardBill, Item, Party, TallyLedgerConfig, Tenant, User
from app.models._mixins import InwardStatus, MatchMethod
from app.schemas_inward import (
    ApproveOut,
    InwardBillListItem,
    InwardBillOut,
    InwardBillPatch,
    InwardLineOut,
    LedgerConfigIO,
    ReconciliationOut,
    RejectRequest,
    SupplierOut,
)
from app.services.inward.approve import ApproveError, approve_bill, approve_gate
from app.services.inward.run_extraction import run_extraction

router = APIRouter(prefix="/api/inward-bills", tags=["inward"])

_MAX_PDF_BYTES = 20 * 1024 * 1024


def _gate(session: SessionDep, user: User) -> User:
    tenant = session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.ext_inward_import:
        # Flag off → the whole module 404s (invisible, not just hidden).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return user


def require_inward(
    session: SessionDep, user: Annotated[User, Depends(get_current_user)]
) -> User:
    return _gate(session, user)


def require_inward_write(
    session: SessionDep, user: Annotated[User, Depends(require_write)]
) -> User:
    return _gate(session, user)


InwardUser = Annotated[User, Depends(require_inward)]
InwardWriteUser = Annotated[User, Depends(require_inward_write)]


def _get_owned(session: SessionDep, tenant_id: str, bill_id: str) -> InwardBill:
    bill = session.scalar(
        select(InwardBill).where(
            InwardBill.id == bill_id, InwardBill.tenant_id == tenant_id
        )
    )
    if bill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill


def _out(session: SessionDep, bill: InwardBill) -> InwardBillOut:
    matched_name = None
    if bill.matched_party_id:
        p = session.get(Party, bill.matched_party_id)
        matched_name = p.legal_name if p else None
    return InwardBillOut(
        id=bill.id,
        source_filename=bill.source_filename,
        status=bill.status,
        bill_no=bill.bill_no,
        bill_date=bill.bill_date,
        sales_order_ref=bill.sales_order_ref,
        amount_in_words=bill.amount_in_words,
        extraction_method=bill.extraction_method,
        extraction_confidence=bill.extraction_confidence,
        error_message=bill.error_message,
        reject_reason=bill.reject_reason,
        tally_xml_path=bill.tally_xml_path,
        created_at=bill.created_at,
        supplier=SupplierOut(
            matched_party_id=bill.matched_party_id,
            matched_party_name=matched_name,
            staged=bill.new_supplier_staged_json,
            supply_type=bill.supply_type,
            place_of_supply_state_code=bill.place_of_supply_state_code,
        ),
        reconciliation=ReconciliationOut(
            reconciled=bill.reconciled,
            discrepancy=bill.reconcile_discrepancy,
            taxable_total=bill.taxable_total,
            cgst_total=bill.cgst_total,
            sgst_total=bill.sgst_total,
            igst_total=bill.igst_total,
            round_off=bill.round_off,
            grand_total=bill.grand_total,
        ),
        lines=[InwardLineOut.model_validate(line) for line in bill.lines],
        approve_blockers=approve_gate(bill),
    )


# --------------------------------------------------------------------------
# list + upload
# --------------------------------------------------------------------------


@router.get("", response_model=list[InwardBillListItem])
def list_bills(
    session: SessionDep,
    user: InwardUser,
    status_: InwardStatus | None = Query(default=None, alias="status"),
    supplier: str | None = Query(default=None),
) -> list[InwardBillListItem]:
    stmt = select(InwardBill).where(InwardBill.tenant_id == user.tenant_id)
    if status_:
        stmt = stmt.where(InwardBill.status == status_)
    if supplier:
        stmt = stmt.where(InwardBill.supplier_name.ilike(f"%{supplier}%"))
    stmt = stmt.order_by(InwardBill.created_at.desc())
    return [
        InwardBillListItem.model_validate(b) for b in session.scalars(stmt).all()
    ]


@router.post("", response_model=list[InwardBillOut], status_code=status.HTTP_201_CREATED)
def upload_bills(
    session: SessionDep,
    user: InwardWriteUser,
    files: list[UploadFile] = File(...),
) -> list[InwardBillOut]:
    settings = get_settings()
    pdf_dir = Path(settings.inward_dir) / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    out: list[InwardBillOut] = []
    for f in files:
        data = f.file.read()
        if len(data) > _MAX_PDF_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{f.filename}: exceeds 20 MB",
            )
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"{f.filename}: PDF only",
            )
        bill_id = str(uuid.uuid4())
        rel = pdf_dir / f"{bill_id}.pdf"
        rel.write_bytes(data)

        bill = InwardBill(
            id=bill_id,
            tenant_id=user.tenant_id,
            uploaded_by=user.id,
            source_filename=f.filename or f"{bill_id}.pdf",
            source_pdf_path=str(rel),
            status=InwardStatus.uploaded,
        )
        session.add(bill)
        session.flush()

        # Single small PDF: extract synchronously (X7 batches via `job`).
        run_extraction(session, bill)
        session.flush()
        out.append(_out(session, bill))
    return out


# --------------------------------------------------------------------------
# detail + pdf
# --------------------------------------------------------------------------


@router.get("/{bill_id}", response_model=InwardBillOut)
def get_bill(
    bill_id: str, session: SessionDep, user: InwardUser
) -> InwardBillOut:
    return _out(session, _get_owned(session, user.tenant_id, bill_id))


@router.get("/{bill_id}/pdf")
def get_bill_pdf(
    bill_id: str, session: SessionDep, user: InwardUser
) -> FileResponse:
    bill = _get_owned(session, user.tenant_id, bill_id)
    if not bill.source_pdf_path or not Path(bill.source_pdf_path).exists():
        raise HTTPException(status_code=404, detail="Source PDF not found")
    return FileResponse(
        bill.source_pdf_path, media_type="application/pdf", filename=bill.source_filename
    )


# --------------------------------------------------------------------------
# patch (reviewer edits)
# --------------------------------------------------------------------------


@router.patch("/{bill_id}", response_model=InwardBillOut)
def patch_bill(
    bill_id: str,
    body: InwardBillPatch,
    session: SessionDep,
    user: InwardWriteUser,
) -> InwardBillOut:
    bill = _get_owned(session, user.tenant_id, bill_id)
    if bill.status in (InwardStatus.approved, InwardStatus.rejected):
        raise HTTPException(status_code=409, detail=f"bill is {bill.status}")

    if body.bill_no is not None:
        bill.bill_no = body.bill_no
    if body.bill_date is not None:
        bill.bill_date = body.bill_date
    if body.place_of_supply_state_code is not None:
        bill.place_of_supply_state_code = body.place_of_supply_state_code

    if body.supplier_matched_party_id is not None:
        party = session.scalar(
            select(Party).where(
                Party.id == body.supplier_matched_party_id,
                Party.tenant_id == user.tenant_id,
            )
        )
        if party is None:
            raise HTTPException(status_code=404, detail="Party not found")
        bill.matched_party_id = party.id
        bill.new_supplier_staged_json = None
    elif body.use_staged_supplier:
        bill.matched_party_id = None

    if body.lines:
        by_sl = {line.sl_no: line for line in bill.lines}
        for patch in body.lines:
            line = by_sl.get(patch.sl_no)
            if line is None:
                continue
            if patch.clear_match:
                line.matched_item_id = None
                line.match_method = None
                line.match_confidence = None
            elif patch.matched_item_id is not None:
                item = session.scalar(
                    select(Item).where(
                        Item.id == patch.matched_item_id,
                        Item.tenant_id == user.tenant_id,
                    )
                )
                if item is None:
                    raise HTTPException(
                        status_code=404, detail=f"line {patch.sl_no}: item not found"
                    )
                line.matched_item_id = item.id
                line.match_method = MatchMethod.manual
                line.match_confidence = None
                line.new_item_staged_json = None
                line.review_flag = None
            if patch.review_flag is not None:
                line.review_flag = patch.review_flag or None

    session.flush()
    return _out(session, bill)


# --------------------------------------------------------------------------
# re-extract / reject / approve
# --------------------------------------------------------------------------


@router.post("/{bill_id}/re-extract", response_model=InwardBillOut)
def re_extract(
    bill_id: str, session: SessionDep, user: InwardWriteUser
) -> InwardBillOut:
    bill = _get_owned(session, user.tenant_id, bill_id)
    if bill.status == InwardStatus.approved:
        raise HTTPException(status_code=409, detail="bill is approved")
    run_extraction(session, bill)
    session.flush()
    return _out(session, bill)


@router.post("/{bill_id}/reject", response_model=InwardBillOut)
def reject(
    bill_id: str,
    body: RejectRequest,
    session: SessionDep,
    user: InwardWriteUser,
) -> InwardBillOut:
    bill = _get_owned(session, user.tenant_id, bill_id)
    if bill.status == InwardStatus.approved:
        raise HTTPException(status_code=409, detail="bill is approved")
    bill.status = InwardStatus.rejected
    bill.reject_reason = body.reason
    session.flush()
    return _out(session, bill)


@router.post("/{bill_id}/approve", response_model=ApproveOut)
def approve(
    bill_id: str, session: SessionDep, user: InwardWriteUser
) -> ApproveOut:
    bill = _get_owned(session, user.tenant_id, bill_id)
    try:
        result = approve_bill(session, bill, actor_user_id=user.id)
    except ApproveError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reasons
        ) from exc
    session.flush()
    return ApproveOut(
        status=bill.status,
        created_supplier_id=result.created_supplier_id,
        promoted_party_id=result.promoted_party_id,
        created_item_ids=result.created_item_ids,
        linked_line_count=result.linked_line_count,
        xml_download_url=f"/api/inward-bills/{bill.id}/xml",
    )


@router.get("/{bill_id}/xml")
def download_xml(
    bill_id: str, session: SessionDep, user: InwardUser
) -> Response:
    bill = _get_owned(session, user.tenant_id, bill_id)
    if bill.status != InwardStatus.approved or not bill.tally_xml_path:
        raise HTTPException(status_code=409, detail="XML is available only after approve")
    path = Path(bill.tally_xml_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="XML file missing")
    return Response(
        content=path.read_bytes(),
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="inward-{bill.bill_no or bill.id}.xml"'
        },
    )


# --------------------------------------------------------------------------
# ledger settings
# --------------------------------------------------------------------------


@router.get("/settings/ledgers", response_model=LedgerConfigIO)
def get_ledgers(
    session: SessionDep, user: InwardUser
) -> LedgerConfigIO:
    cfg = session.get(TallyLedgerConfig, user.tenant_id)
    if cfg is None:
        cfg = TallyLedgerConfig(tenant_id=user.tenant_id)
        session.add(cfg)
        session.flush()
    return LedgerConfigIO.model_validate(cfg)


@router.put("/settings/ledgers", response_model=LedgerConfigIO)
def put_ledgers(
    body: LedgerConfigIO,
    session: SessionDep,
    user: InwardWriteUser,
) -> LedgerConfigIO:
    cfg = session.get(TallyLedgerConfig, user.tenant_id)
    if cfg is None:
        cfg = TallyLedgerConfig(tenant_id=user.tenant_id)
        session.add(cfg)
    for field_name, value in body.model_dump().items():
        setattr(cfg, field_name, value)
    session.flush()
    return LedgerConfigIO.model_validate(cfg)

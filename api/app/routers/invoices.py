"""Sales invoice API — /api/invoices, scoped to the caller's tenant.

A draft is created without a number and edited as a whole (`PUT` replaces
header + all lines). `POST /{id}/finalize` runs the finalize transaction
(number claim, freeze totals, item accretion, Loop-2 category learning,
PDF). Finalized invoices are immutable; `cancel` sets status without
reusing the number.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Invoice, InvoiceLine, Party
from app.models._mixins import DocType, InvoiceStatus, PdfStatus
from app.schemas_invoice import (
    DuplicateOut,
    FinalizeOut,
    InvoiceCreate,
    InvoiceLineOut,
    InvoiceListItem,
    InvoiceOut,
    InvoiceUpdate,
    PartyBrief,
)
from app.services.invoices.common import (
    finalize_blockers,
    financial_year,
    totals_for,
)
from app.services.invoices.finalize import FinalizeError, finalize_invoice

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

_EDITABLE_STATUSES = {InvoiceStatus.draft}


def _load(session: SessionDep, tenant_id: str, invoice_id: str) -> Invoice:
    inv = session.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
        .options(selectinload(Invoice.lines))
    )
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return inv


def _owned_party(session: SessionDep, tenant_id: str, party_id: str) -> Party:
    p = session.scalar(
        select(Party).where(Party.id == party_id, Party.tenant_id == tenant_id)
    )
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Party not found")
    return p


def _apply_lines(inv: Invoice, lines: list) -> None:
    inv.lines.clear()
    for i, ln in enumerate(lines, start=1):
        inv.lines.append(
            InvoiceLine(
                sl_no=i,
                item_id=ln.item_id,
                description=ln.description.strip(),
                hsn_code=ln.hsn_code,
                quantity=ln.quantity,
                uom=ln.uom,
                unit_rate=ln.unit_rate,
                discount=ln.discount or Decimal("0"),
                size_pos=ln.size_pos,
            )
        )


def _out(session: SessionDep, inv: Invoice) -> InvoiceOut:
    party = session.get(Party, inv.party_id) if inv.party_id else None
    return InvoiceOut(
        id=inv.id,
        doc_type=inv.doc_type,
        series=inv.series,
        number=inv.number,
        fy=inv.fy,
        date=inv.date,
        status=inv.status,
        template_version=inv.template_version,
        party_id=inv.party_id,
        party=PartyBrief.model_validate(party) if party else None,
        bill_to_addr_id=inv.bill_to_addr_id,
        ship_to_addr_id=inv.ship_to_addr_id,
        notes=inv.notes,
        terms_snapshot=inv.terms_snapshot,
        declaration_snapshot=inv.declaration_snapshot,
        invoice_discount=inv.invoice_discount or Decimal("0"),
        totals=totals_for(inv),
        pdf_status=inv.pdf_status,
        has_pdf=bool(inv.pdf_path),
        lines=[InvoiceLineOut.model_validate(ln) for ln in inv.lines],
        finalize_blockers=finalize_blockers(inv) if inv.status == InvoiceStatus.draft else [],
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


@router.get("", response_model=list[InvoiceListItem])
def list_invoices(
    user: CurrentUser,
    session: SessionDep,
    status_: InvoiceStatus | None = Query(default=None, alias="status"),
    party_id: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    q: str | None = Query(default=None, description="party name contains"),
) -> list[InvoiceListItem]:
    stmt = (
        select(Invoice, Party.legal_name)
        .join(Party, Party.id == Invoice.party_id)
        .where(Invoice.tenant_id == user.tenant_id)
    )
    if status_ is not None:
        stmt = stmt.where(Invoice.status == status_)
    if party_id:
        stmt = stmt.where(Invoice.party_id == party_id)
    if date_from:
        stmt = stmt.where(Invoice.date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.date <= date_to)
    if q:
        stmt = stmt.where(func.lower(Party.legal_name).like(f"%{q.lower().strip()}%"))
    stmt = stmt.order_by(
        (Invoice.status == InvoiceStatus.draft).desc(),
        Invoice.number.desc().nullsfirst(),
        Invoice.date.desc(),
        Invoice.created_at.desc(),
    )
    rows = session.execute(stmt).all()
    return [
        InvoiceListItem(
            id=inv.id,
            number=inv.number,
            fy=inv.fy,
            date=inv.date,
            status=inv.status,
            party_id=inv.party_id,
            party_name=name,
            grand_total=inv.grand_total,
            pdf_status=inv.pdf_status,
        )
        for inv, name in rows
    ]


# --------------------------------------------------------------------------
# create / read / update / delete
# --------------------------------------------------------------------------


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceCreate, user: WriteUser, session: SessionDep) -> InvoiceOut:
    _owned_party(session, user.tenant_id, body.party_id)
    d = body.date or date.today()
    inv = Invoice(
        tenant_id=user.tenant_id,
        doc_type=DocType.inv,
        series="Sales",
        fy=financial_year(d),
        date=d,
        party_id=body.party_id,
        bill_to_addr_id=body.bill_to_addr_id,
        ship_to_addr_id=body.ship_to_addr_id,
        notes=body.notes,
        invoice_discount=body.invoice_discount or Decimal("0"),
        status=InvoiceStatus.draft,
        pdf_status=PdfStatus.none,
    )
    _apply_lines(inv, body.lines)
    session.add(inv)
    session.flush()
    return _out(session, inv)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: str, user: CurrentUser, session: SessionDep) -> InvoiceOut:
    return _out(session, _load(session, user.tenant_id, invoice_id))


@router.put("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    invoice_id: str, body: InvoiceUpdate, user: WriteUser, session: SessionDep
) -> InvoiceOut:
    inv = _load(session, user.tenant_id, invoice_id)
    if inv.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"invoice is {inv.status} and cannot be edited",
        )

    if body.party_id is not None and body.party_id != inv.party_id:
        _owned_party(session, user.tenant_id, body.party_id)
        inv.party_id = body.party_id
    if body.date is not None:
        inv.date = body.date
        inv.fy = financial_year(body.date)
    if body.bill_to_addr_id is not None:
        inv.bill_to_addr_id = body.bill_to_addr_id or None
    if body.ship_to_addr_id is not None:
        inv.ship_to_addr_id = body.ship_to_addr_id or None
    if body.notes is not None:
        inv.notes = body.notes
    if body.invoice_discount is not None:
        inv.invoice_discount = body.invoice_discount
    if body.lines is not None:
        _apply_lines(inv, body.lines)

    session.flush()
    return _out(session, inv)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: str, user: WriteUser, session: SessionDep) -> None:
    inv = _load(session, user.tenant_id, invoice_id)
    if inv.status != InvoiceStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only a draft can be deleted; cancel a finalized invoice instead",
        )
    session.delete(inv)


# --------------------------------------------------------------------------
# finalize / cancel / duplicate
# --------------------------------------------------------------------------


@router.post("/{invoice_id}/finalize", response_model=FinalizeOut)
def finalize(invoice_id: str, user: WriteUser, session: SessionDep) -> FinalizeOut:
    inv = _load(session, user.tenant_id, invoice_id)
    try:
        result = finalize_invoice(session, inv, actor_user_id=user.id)
    except FinalizeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reasons
        ) from exc
    session.flush()
    return FinalizeOut(
        id=inv.id,
        number=result.number,
        fy=result.fy,
        status=inv.status,
        totals=totals_for(inv),
        pdf_status=result.pdf_status,
        created_item_ids=result.created_item_ids,
        learned_group_ids=result.learned_group_ids,
    )


@router.post("/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(invoice_id: str, user: WriteUser, session: SessionDep) -> InvoiceOut:
    inv = _load(session, user.tenant_id, invoice_id)
    if inv.status == InvoiceStatus.cancelled:
        return _out(session, inv)
    if inv.status == InvoiceStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a draft has no number to cancel — delete it instead",
        )
    inv.status = InvoiceStatus.cancelled
    session.flush()
    return _out(session, inv)


@router.post("/{invoice_id}/duplicate", response_model=DuplicateOut, status_code=201)
def duplicate_invoice(invoice_id: str, user: WriteUser, session: SessionDep) -> DuplicateOut:
    src = _load(session, user.tenant_id, invoice_id)
    d = date.today()
    clone = Invoice(
        tenant_id=user.tenant_id,
        doc_type=DocType.inv,
        series="Sales",
        fy=financial_year(d),
        date=d,
        party_id=src.party_id,
        bill_to_addr_id=src.bill_to_addr_id,
        ship_to_addr_id=src.ship_to_addr_id,
        notes=src.notes,
        invoice_discount=src.invoice_discount or Decimal("0"),
        status=InvoiceStatus.draft,
        pdf_status=PdfStatus.none,
    )
    for ln in sorted(src.lines, key=lambda x: x.sl_no):
        clone.lines.append(
            InvoiceLine(
                sl_no=ln.sl_no,
                item_id=ln.item_id,
                description=ln.description,
                hsn_code=ln.hsn_code,
                quantity=ln.quantity,
                uom=ln.uom,
                unit_rate=ln.unit_rate,
                discount=ln.discount,
                size_pos=ln.size_pos,
            )
        )
    session.add(clone)
    session.flush()
    return DuplicateOut(id=clone.id)


# --------------------------------------------------------------------------
# pdf
# --------------------------------------------------------------------------


@router.get("/{invoice_id}/pdf")
def get_pdf(invoice_id: str, user: CurrentUser, session: SessionDep) -> FileResponse:
    inv = _load(session, user.tenant_id, invoice_id)
    from pathlib import Path

    if not inv.pdf_path or not Path(inv.pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF not available — try re-render")
    fname = f"invoice-{inv.number or inv.id}.pdf"
    return FileResponse(inv.pdf_path, media_type="application/pdf", filename=fname)


@router.post("/{invoice_id}/rerender", response_model=InvoiceOut)
def rerender_pdf(invoice_id: str, user: WriteUser, session: SessionDep) -> InvoiceOut:
    inv = _load(session, user.tenant_id, invoice_id)
    if inv.status != InvoiceStatus.final:
        raise HTTPException(status_code=409, detail="only a finalized invoice has a PDF")
    from app.services.invoices.pdf import render_invoice_pdf

    render_invoice_pdf(session, inv)
    session.flush()
    return _out(session, inv)

"""Payments API — /api/payments, scoped to the caller's tenant.

A payment is created posted (no draft/finalize split) with its allocations
in one transaction. Every allocation against an invoice is re-validated
against that invoice's *live* balance_due inside the transaction (never
trusting a client-sent balance) with a row lock on the invoice(s) being
allocated against, so two concurrent payments can't both allocate more than
what's left. If the client's allocations sum to less than the payment
amount, the remainder becomes an on_account allocation, created server-side.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Invoice, Party, Payment, PaymentAllocation
from app.models._mixins import AllocationType, InvoiceStatus, PaymentMode, PaymentStatus
from app.schemas_payments import (
    CollectionsRow,
    PartyLedgerEntry,
    PaymentAllocationOut,
    PaymentCreate,
    PaymentOut,
    ReversePaymentIn,
)
from app.services.payments import (
    balance_due_for_invoice,
    claim_voucher_no,
    collections_summary,
    party_ledger,
)

router = APIRouter(prefix="/api/payments", tags=["payments"])

_LEDGER_NAME_DEFAULTS = {
    PaymentMode.cash: "Cash",
    PaymentMode.upi: "Bank",
    PaymentMode.bank: "Bank",
    PaymentMode.cheque: "Bank",
}


def _load(session: SessionDep, tenant_id: str, payment_id: str) -> Payment:
    pay = session.scalar(
        select(Payment)
        .where(Payment.id == payment_id, Payment.tenant_id == tenant_id)
        .options(selectinload(Payment.allocations))
    )
    if pay is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return pay


def _owned_party(session: SessionDep, tenant_id: str, party_id: str) -> Party:
    p = session.scalar(select(Party).where(Party.id == party_id, Party.tenant_id == tenant_id))
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Party not found")
    return p


def _out(session: SessionDep, pay: Payment) -> PaymentOut:
    party = session.get(Party, pay.party_id)
    alloc_out: list[PaymentAllocationOut] = []
    for a in pay.allocations:
        inv_number = None
        if a.invoice_id:
            inv = session.get(Invoice, a.invoice_id)
            inv_number = inv.number if inv else None
        alloc_out.append(
            PaymentAllocationOut(
                id=a.id,
                invoice_id=a.invoice_id,
                invoice_number=inv_number,
                type=a.type,
                amount=a.amount,
            )
        )
    return PaymentOut(
        id=pay.id,
        party_id=pay.party_id,
        party_name=party.legal_name if party else None,
        date=pay.date,
        amount=pay.amount,
        mode=pay.mode,
        ref_no=pay.ref_no,
        notes=pay.notes,
        voucher_no=pay.voucher_no,
        ledger_name=pay.ledger_name,
        status=pay.status,
        reversed_at=pay.reversed_at,
        reversed_reason=pay.reversed_reason,
        allocations=alloc_out,
        created_at=pay.created_at,
        updated_at=pay.updated_at,
    )


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_payment(body: PaymentCreate, user: WriteUser, session: SessionDep) -> PaymentOut:
    party = _owned_party(session, user.tenant_id, body.party_id)

    sum_allocated = sum((a.amount for a in body.allocations), Decimal("0"))
    if sum_allocated > body.amount:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"allocations total {sum_allocated} exceeds payment amount {body.amount}"
            ),
        )

    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

    pay = Payment(
        tenant_id=user.tenant_id,
        party_id=party.id,
        date=body.date or date.today(),
        amount=body.amount,
        mode=body.mode,
        ref_no=body.ref_no,
        notes=body.notes,
        ledger_name=body.ledger_name or _LEDGER_NAME_DEFAULTS.get(body.mode),
        status=PaymentStatus.posted,
    )
    session.add(pay)
    session.flush()

    for a in body.allocations:
        if a.type == AllocationType.against_invoice:
            if not a.invoice_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="against_invoice allocation requires invoice_id",
                )
            inv_stmt = select(Invoice).where(
                Invoice.id == a.invoice_id,
                Invoice.tenant_id == user.tenant_id,
                Invoice.party_id == party.id,
            )
            if is_pg:
                inv_stmt = inv_stmt.with_for_update()
            inv = session.scalar(inv_stmt)
            # IDOR guard: the invoice must exist, belong to THIS tenant AND
            # THIS party — a mismatch on either surfaces as a plain 404
            # rather than leaking which field failed.
            if inv is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"invoice {a.invoice_id} not found for this party",
                )
            if inv.status != InvoiceStatus.final:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"invoice {inv.id} is not finalized — cannot allocate against it",
                )
            live_balance = balance_due_for_invoice(session, inv)
            if a.amount > live_balance:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"allocation of {a.amount} exceeds invoice {inv.id}'s "
                        f"current balance_due {live_balance}"
                    ),
                )
            session.add(
                PaymentAllocation(
                    payment_id=pay.id,
                    invoice_id=inv.id,
                    type=AllocationType.against_invoice,
                    amount=a.amount,
                )
            )
        else:  # on_account
            if a.invoice_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="on_account allocation must not set invoice_id",
                )
            session.add(
                PaymentAllocation(
                    payment_id=pay.id,
                    invoice_id=None,
                    type=AllocationType.on_account,
                    amount=a.amount,
                )
            )
        session.flush()

    remainder = body.amount - sum_allocated
    if remainder > 0:
        session.add(
            PaymentAllocation(
                payment_id=pay.id,
                invoice_id=None,
                type=AllocationType.on_account,
                amount=remainder,
            )
        )
        session.flush()

    pay.voucher_no = claim_voucher_no(session, user.tenant_id)
    session.flush()

    session.refresh(pay)
    return _out(session, pay)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: str, user: CurrentUser, session: SessionDep) -> PaymentOut:
    return _out(session, _load(session, user.tenant_id, payment_id))


@router.post("/{payment_id}/reverse", response_model=PaymentOut)
def reverse_payment(
    payment_id: str, body: ReversePaymentIn, user: WriteUser, session: SessionDep
) -> PaymentOut:
    from datetime import UTC, datetime

    pay = _load(session, user.tenant_id, payment_id)
    if pay.status == PaymentStatus.reversed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="payment is already reversed"
        )
    pay.status = PaymentStatus.reversed
    pay.reversed_at = datetime.now(UTC)
    pay.reversed_reason = body.reason
    session.flush()
    return _out(session, pay)


# --------------------------------------------------------------------------
# collections — no GET /api/payments list endpoint was requested; only
# create/get/reverse live on `router`. This is a separate router mounted at
# /api/collections.
# --------------------------------------------------------------------------

collections_router = APIRouter(prefix="/api/collections", tags=["payments"])


@collections_router.get("", response_model=list[CollectionsRow])
def collections(
    user: CurrentUser,
    session: SessionDep,
    sort: str = Query(default="balance", pattern="^(balance|oldest)$"),
    q: str | None = Query(default=None),
) -> list[CollectionsRow]:
    rows = collections_summary(session, user.tenant_id, sort=sort, q=q)  # type: ignore[arg-type]
    return [
        CollectionsRow(
            party_id=r.party_id,
            legal_name=r.legal_name,
            phone=r.phone,
            outstanding_balance=r.outstanding_balance,
            oldest_unpaid_days=r.oldest_unpaid_days,
            open_invoice_count=r.open_invoice_count,
        )
        for r in rows
    ]


# --------------------------------------------------------------------------
# party ledger statement — mounted here, party-scoped path, since it needs
# the same PartyLedgerEntry schema as this module
# --------------------------------------------------------------------------

ledger_router = APIRouter(prefix="/api/parties", tags=["payments"])


@ledger_router.get("/{party_id}/ledger", response_model=list[PartyLedgerEntry])
def get_party_ledger(party_id: str, user: CurrentUser, session: SessionDep) -> list[PartyLedgerEntry]:
    _owned_party(session, user.tenant_id, party_id)
    entries = party_ledger(session, party_id)
    return [
        PartyLedgerEntry(
            kind=e.kind,
            date=e.date,
            ref_id=e.ref_id,
            ref_label=e.ref_label,
            debit=e.debit,
            credit=e.credit,
            running_balance=e.running_balance,
            status=e.status,
            allocations=e.allocations,
        )
        for e in entries
    ]

"""Payment / party-ledger balance computation. Pure query functions — no
side effects — so they're callable from routers and from the Collections
aggregate alike.

Reversal design: a reversed payment's `payment_allocation` rows are left in
place (audit trail); every sum here filters `Payment.status ==
PaymentStatus.posted`, so a reversed payment's allocations stop counting
automatically without deleting anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Invoice, Party, Payment, PaymentAllocation
from app.models._mixins import AllocationType, InvoiceStatus, PaymentStatus

_ZERO = Decimal("0.00")

PaymentStatusLabel = Literal["unpaid", "partial", "paid"]


def paid_amount_for_invoice(session: Session, invoice_id: str) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.invoice_id == invoice_id,
            PaymentAllocation.type == AllocationType.against_invoice,
            Payment.status == PaymentStatus.posted,
        )
    )
    return Decimal(total or 0)


def balance_due_for_invoice(session: Session, invoice: Invoice) -> Decimal:
    """Only meaningful for a finalized invoice — a draft/cancelled invoice
    has no frozen grand_total to bill against and cannot receive allocations
    (see the router guard).
    """
    total = invoice.grand_total if invoice.grand_total is not None else _ZERO
    return total - paid_amount_for_invoice(session, invoice.id)


def invoice_payment_status(session: Session, invoice: Invoice) -> PaymentStatusLabel:
    total = invoice.grand_total if invoice.grand_total is not None else _ZERO
    paid = paid_amount_for_invoice(session, invoice.id)
    if paid <= 0:
        return "unpaid"
    if paid >= total:
        return "paid"
    return "partial"


def on_account_balance_for_party(session: Session, party_id: str) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            Payment.party_id == party_id,
            PaymentAllocation.type == AllocationType.on_account,
            Payment.status == PaymentStatus.posted,
        )
    )
    return Decimal(total or 0)


def outstanding_balance_for_party(session: Session, party_id: str) -> Decimal:
    """Sum of balance_due across the party's finalized invoices, minus any
    on-account credit. Returned as a raw signed value (a net-credit party
    comes back negative) — the caller/frontend decides how to display that.
    """
    invoices = session.scalars(
        select(Invoice).where(
            Invoice.party_id == party_id, Invoice.status == InvoiceStatus.final
        )
    ).all()
    gross = sum((balance_due_for_invoice(session, inv) for inv in invoices), _ZERO)
    return gross - on_account_balance_for_party(session, party_id)


def open_invoices_for_party(session: Session, party_id: str) -> list[Invoice]:
    """Finalized invoices for this party with balance_due > 0, oldest first
    (matches invoice numbering order) — feeds the FIFO-default allocation
    list in the payment dialog.
    """
    invoices = session.scalars(
        select(Invoice)
        .where(Invoice.party_id == party_id, Invoice.status == InvoiceStatus.final)
        .order_by(Invoice.date.asc(), Invoice.number.asc())
    ).all()
    return [inv for inv in invoices if balance_due_for_invoice(session, inv) > 0]


# --------------------------------------------------------------------------
# Collections list — one aggregate query, no N+1 across parties.
# --------------------------------------------------------------------------


@dataclass
class CollectionsSummaryRow:
    party_id: str
    legal_name: str
    phone: str | None
    outstanding_balance: Decimal
    oldest_unpaid_days: int | None
    open_invoice_count: int


def collections_summary(
    session: Session,
    tenant_id: str,
    *,
    sort: Literal["balance", "oldest"] = "balance",
    q: str | None = None,
) -> list[CollectionsSummaryRow]:
    """One round trip: per-party (gross balance_due, oldest unpaid invoice
    date, open invoice count) via a paid-subquery join, then subtract
    on-account credit per party via a second aggregate. Only parties with a
    net outstanding_balance > 0 are returned.
    """
    paid_sq = (
        select(
            PaymentAllocation.invoice_id.label("invoice_id"),
            func.sum(PaymentAllocation.amount).label("paid"),
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.type == AllocationType.against_invoice,
            Payment.status == PaymentStatus.posted,
        )
        .group_by(PaymentAllocation.invoice_id)
        .subquery()
    )

    balance_due_expr = Invoice.grand_total - func.coalesce(paid_sq.c.paid, 0)

    inv_stmt = (
        select(
            Invoice.party_id.label("party_id"),
            func.sum(balance_due_expr).label("gross_balance"),
            func.min(Invoice.date).label("oldest_date"),
            func.count(Invoice.id).label("open_invoice_count"),
        )
        .outerjoin(paid_sq, paid_sq.c.invoice_id == Invoice.id)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.status == InvoiceStatus.final,
            balance_due_expr > 0,
        )
        .group_by(Invoice.party_id)
        .subquery()
    )

    credit_sq = (
        select(
            Payment.party_id.label("party_id"),
            func.sum(PaymentAllocation.amount).label("credit"),
        )
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .where(
            Payment.tenant_id == tenant_id,
            PaymentAllocation.type == AllocationType.on_account,
            Payment.status == PaymentStatus.posted,
        )
        .group_by(Payment.party_id)
        .subquery()
    )

    net_balance_expr = inv_stmt.c.gross_balance - func.coalesce(credit_sq.c.credit, 0)

    stmt = (
        select(
            Party.id,
            Party.legal_name,
            Party.phone,
            net_balance_expr.label("outstanding_balance"),
            inv_stmt.c.oldest_date,
            inv_stmt.c.open_invoice_count,
        )
        .join(inv_stmt, inv_stmt.c.party_id == Party.id)
        .outerjoin(credit_sq, credit_sq.c.party_id == Party.id)
        .where(net_balance_expr > 0)
    )
    if q:
        stmt = stmt.where(func.lower(Party.legal_name).like(f"%{q.lower().strip()}%"))

    if sort == "oldest":
        stmt = stmt.order_by(inv_stmt.c.oldest_date.asc())
    else:
        stmt = stmt.order_by(net_balance_expr.desc())

    today = date.today()
    rows = session.execute(stmt).all()
    out: list[CollectionsSummaryRow] = []
    for party_id, legal_name, phone, balance, oldest_date, open_count in rows:
        oldest_days = (today - oldest_date).days if oldest_date else None
        out.append(
            CollectionsSummaryRow(
                party_id=party_id,
                legal_name=legal_name,
                phone=phone,
                outstanding_balance=Decimal(balance or 0),
                oldest_unpaid_days=oldest_days,
                open_invoice_count=int(open_count or 0),
            )
        )
    return out


# --------------------------------------------------------------------------
# Party ledger statement
# --------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    kind: Literal["invoice", "payment"]
    date: date
    ref_id: str
    ref_label: str
    debit: Decimal  # increases what the party owes (an invoice)
    credit: Decimal  # decreases what the party owes (a payment)
    running_balance: Decimal
    status: str
    allocations: list[dict] | None = None


def party_ledger(session: Session, party_id: str) -> list[LedgerEntry]:
    """Chronological statement: finalized invoices (debit) + posted/reversed
    payments (credit, 0 for reversed) interleaved by date.

    Running balance is computed walking oldest -> newest (the only order in
    which "running balance" is unambiguous), then the list is reversed for
    display (newest first, bank-statement convention) — comment kept next to
    the reverse() call below so the two don't drift apart.
    """
    invoices = session.scalars(
        select(Invoice).where(
            Invoice.party_id == party_id, Invoice.status == InvoiceStatus.final
        )
    ).all()
    payments = session.scalars(
        select(Payment).where(Payment.party_id == party_id)
    ).all()

    events: list[tuple[date, int, str, object]] = []
    for inv in invoices:
        events.append((inv.date, 0, inv.id, inv))
    for pay in payments:
        # sub-sort payments after invoices on the same date (index 1) — an
        # arbitrary but stable tie-break, doesn't affect the final balance
        events.append((pay.date, 1, pay.id, pay))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    running = _ZERO
    entries: list[LedgerEntry] = []
    for _d, _tie, _id, obj in events:
        if isinstance(obj, Invoice):
            amt = obj.grand_total or _ZERO
            running += amt
            entries.append(
                LedgerEntry(
                    kind="invoice",
                    date=obj.date,
                    ref_id=obj.id,
                    ref_label=f"INV #{obj.number}" if obj.number else "INV (draft)",
                    debit=amt,
                    credit=_ZERO,
                    running_balance=running,
                    status=obj.status,
                )
            )
        else:
            pay = obj
            is_reversed = pay.status == PaymentStatus.reversed
            credit_amt = _ZERO if is_reversed else pay.amount
            running -= credit_amt
            alloc_out = [
                {
                    "invoice_id": a.invoice_id,
                    "type": a.type,
                    "amount": str(a.amount),
                }
                for a in pay.allocations
            ]
            entries.append(
                LedgerEntry(
                    kind="payment",
                    date=pay.date,
                    ref_id=pay.id,
                    ref_label=f"PMT #{pay.voucher_no}" if pay.voucher_no else "PMT",
                    debit=_ZERO,
                    credit=credit_amt,
                    running_balance=running,
                    status=pay.status,
                    allocations=alloc_out,
                )
            )

    # newest-first for display (bank-statement convention) — running_balance
    # values were computed walking forward above, so this reverse is purely
    # cosmetic ordering and does not touch the numbers.
    entries.reverse()
    return entries


# --------------------------------------------------------------------------
# Voucher numbering — mirrors invoice.number via the shared number_sequence
# table, using a dedicated series so it never collides with invoice numbers.
# Payments have no series/FY concept, so `fy` is pinned to a constant.
# --------------------------------------------------------------------------

PAYMENT_SERIES = "Payment"
PAYMENT_FY = "ALL"


def claim_voucher_no(session: Session, tenant_id: str) -> int:
    from app.models import NumberSequence

    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
    stmt = select(NumberSequence).where(
        NumberSequence.tenant_id == tenant_id,
        NumberSequence.series == PAYMENT_SERIES,
        NumberSequence.fy == PAYMENT_FY,
    )
    if is_pg:
        stmt = stmt.with_for_update()
    row = session.scalar(stmt)
    if row is None:
        row = NumberSequence(
            tenant_id=tenant_id, series=PAYMENT_SERIES, fy=PAYMENT_FY, last_value=0
        )
        session.add(row)
        session.flush()
        if is_pg:
            row = session.scalar(stmt)  # re-select under lock
    assert row is not None
    row.last_value += 1
    session.flush()
    return row.last_value


__all__ = [
    "paid_amount_for_invoice",
    "balance_due_for_invoice",
    "invoice_payment_status",
    "on_account_balance_for_party",
    "outstanding_balance_for_party",
    "open_invoices_for_party",
    "collections_summary",
    "CollectionsSummaryRow",
    "party_ledger",
    "LedgerEntry",
    "claim_voucher_no",
]

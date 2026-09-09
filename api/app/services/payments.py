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
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Invoice, Party, Payment, PaymentAllocation, Tenant
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


def has_ledger_history(session: Session, party_id: str) -> bool:
    """True once the party has anything that pins a ledger position: a
    finalized invoice OR any payment row (posted or reversed — a reversed
    payment still means the party was transacted). Draft/cancelled invoices
    don't count. Gates whether `party.opening_balance` may still be edited.
    """
    has_invoice = session.scalar(
        select(Invoice.id)
        .where(Invoice.party_id == party_id, Invoice.status == InvoiceStatus.final)
        .limit(1)
    )
    if has_invoice is not None:
        return True
    has_payment = session.scalar(
        select(Payment.id).where(Payment.party_id == party_id).limit(1)
    )
    return has_payment is not None


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


def opening_balance_for_party(session: Session, party_id: str) -> Decimal:
    party = session.get(Party, party_id)
    return Decimal(party.opening_balance or 0) if party is not None else _ZERO


def outstanding_balance_for_party(session: Session, party_id: str) -> Decimal:
    """Party's opening balance plus balance_due across finalized invoices,
    minus any on-account credit. Returned as a raw signed value (a net-credit
    party comes back negative) — the caller/frontend decides how to display
    that.
    """
    invoices = session.scalars(
        select(Invoice).where(
            Invoice.party_id == party_id, Invoice.status == InvoiceStatus.final
        )
    ).all()
    gross = sum((balance_due_for_invoice(session, inv) for inv in invoices), _ZERO)
    return (
        opening_balance_for_party(session, party_id)
        + gross
        - on_account_balance_for_party(session, party_id)
    )


def previous_outstanding_for_party(
    session: Session, party_id: str, *, exclude_invoice_id: str
) -> Decimal:
    """The party's outstanding balance NOT counting one invoice — used on that
    invoice's PDF as "previous outstanding before this bill". It's the opening
    balance plus the balance_due of every *other* finalized invoice, minus
    on-account credit. Signed (a net-credit party is negative).

    Note this uses each other invoice's *live* balance_due, so a payment later
    made against an earlier bill correctly reduces the "previous" figure on a
    re-render.
    """
    invoices = session.scalars(
        select(Invoice).where(
            Invoice.party_id == party_id,
            Invoice.status == InvoiceStatus.final,
            Invoice.id != exclude_invoice_id,
        )
    ).all()
    gross = sum((balance_due_for_invoice(session, inv) for inv in invoices), _ZERO)
    return (
        opening_balance_for_party(session, party_id)
        + gross
        - on_account_balance_for_party(session, party_id)
    )


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
    # positive = party owes us; negative = we owe the party (on-account credit
    # exceeds what's billed); zero rows are never returned.
    outstanding_balance: Decimal
    oldest_unpaid_days: int | None
    open_invoice_count: int


CollectionsScope = Literal["outstanding", "overpaid", "either"]


def collections_summary(
    session: Session,
    tenant_id: str,
    *,
    scope: CollectionsScope = "outstanding",
    sort: Literal["balance", "oldest"] = "balance",
    q: str | None = None,
) -> list[CollectionsSummaryRow]:
    """One round trip: per-party gross balance_due (party-driven LEFT JOINs,
    so a party with only an on-account credit and no open invoice still
    surfaces) minus on-account credit = net outstanding_balance. `scope`
    selects which sign(s) to return:

      * "outstanding" — balance > 0 (they owe us) — the everyday collections view
      * "overpaid"     — balance < 0 (we owe them, e.g. an unapplied credit
                          with nothing left to apply it to)
      * "either"        — both, |balance| > 0

    A party with balance == 0 (fully settled) is never returned in any scope.
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

    inv_sq = (
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

    # opening_balance is a plain column on Party (positive = owes us), folded
    # in so a party whose ONLY balance is a pre-go-live opening figure still
    # surfaces in the "outstanding" scope.
    net_balance_expr = (
        func.coalesce(Party.opening_balance, 0)
        + func.coalesce(inv_sq.c.gross_balance, 0)
        - func.coalesce(credit_sq.c.credit, 0)
    )

    # party-driven: LEFT JOIN both sides so a party with ONLY an on-account
    # credit (no open invoice at all) still gets a row — an INNER JOIN to
    # inv_sq (the old shape) structurally can't surface that party.
    stmt = (
        select(
            Party.id,
            Party.legal_name,
            Party.phone,
            net_balance_expr.label("outstanding_balance"),
            inv_sq.c.oldest_date,
            inv_sq.c.open_invoice_count,
        )
        .select_from(Party)
        .outerjoin(inv_sq, inv_sq.c.party_id == Party.id)
        .outerjoin(credit_sq, credit_sq.c.party_id == Party.id)
        .where(Party.tenant_id == tenant_id)
    )
    if scope == "outstanding":
        stmt = stmt.where(net_balance_expr > 0)
    elif scope == "overpaid":
        stmt = stmt.where(net_balance_expr < 0)
    else:
        stmt = stmt.where(net_balance_expr != 0)

    if q:
        stmt = stmt.where(func.lower(Party.legal_name).like(f"%{q.lower().strip()}%"))

    if sort == "oldest":
        # overpaid-only rows have no oldest_date (no open invoice) — push
        # them last within a scope that can mix signs.
        stmt = stmt.order_by(inv_sq.c.oldest_date.is_(None), inv_sq.c.oldest_date.asc())
    else:
        # sort by distance from zero either direction ("biggest balance,
        # whichever way it points") so "either" reads sensibly too.
        stmt = stmt.order_by(func.abs(net_balance_expr).desc())

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
# Collections ageing dashboard (F3a) — per-party ageing buckets.
#
# Buckets are keyed lt30 / d30 / d60 / d90p; the UI labels them
# "<30 days / 30+ / 60+ / >3 months" (display-only, mutually exclusive).
# An invoice's balance ages by its DUE date = invoice.date +
# tenant.default_credit_days. Opening balance ages by opening_balance_as_of
# (falls into lt30 when that's null). Bucketing is done in Python so the
# date arithmetic is identical on SQLite and Postgres.
# --------------------------------------------------------------------------

AgeingBucket = Literal["lt30", "d30", "d60", "d90p"]

_BUCKET_KEYS: tuple[AgeingBucket, ...] = ("lt30", "d30", "d60", "d90p")


def _bucket_for_age(age_days: int) -> AgeingBucket:
    """age_days = as_on - due_date. <=29 -> lt30, 30-59 -> d30,
    60-89 -> d60, >=90 -> d90p. Not-yet-due (negative age) is lt30.
    """
    if age_days <= 29:
        return "lt30"
    if age_days <= 59:
        return "d30"
    if age_days <= 89:
        return "d60"
    return "d90p"


@dataclass
class AgeingRow:
    party_id: str
    legal_name: str
    phone: str | None
    lt30: Decimal
    d30: Decimal
    d60: Decimal
    d90p: Decimal
    total: Decimal
    worst_bucket: AgeingBucket
    is_overdue: bool  # worst_bucket != "lt30"
    oldest_bill_date: date | None
    oldest_bill_number: int | None
    last_payment_date: date | None
    open_invoice_count: int


def ageing_summary(
    session: Session,
    tenant_id: str,
    *,
    as_on: date | None = None,
    q: str | None = None,
) -> list[AgeingRow]:
    """Per-party ageing buckets for the Collections dashboard.

    One query pulls every open (finalized, balance_due > 0) invoice's
    (party, date, number, live balance); a second pulls each party's last
    posted-payment date; a third the opening balances. Bucketing + the
    running totals are done in Python. A party surfaces if it has any
    positive net (open invoices and/or an opening debit); a net-credit or
    net-zero party is omitted (mirrors `collections_summary`'s "outstanding"
    scope — ageing is only meaningful for money owed to us).
    """
    as_on = as_on or date.today()

    tenant = session.get(Tenant, tenant_id)
    credit_days = int(tenant.default_credit_days) if tenant is not None else 0

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

    inv_rows = session.execute(
        select(
            Invoice.party_id,
            Invoice.date,
            Invoice.number,
            balance_due_expr.label("balance_due"),
        )
        .outerjoin(paid_sq, paid_sq.c.invoice_id == Invoice.id)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.status == InvoiceStatus.final,
            balance_due_expr > 0,
        )
    ).all()

    last_pay_rows = session.execute(
        select(Payment.party_id, func.max(Payment.date))
        .where(
            Payment.tenant_id == tenant_id,
            Payment.status == PaymentStatus.posted,
        )
        .group_by(Payment.party_id)
    ).all()
    last_payment: dict[str, date] = {pid: d for pid, d in last_pay_rows if d is not None}

    # on-account credit reduces the net but never a bucket (matches the
    # display rule in the F3b/F3a review: an opening credit / advance is not
    # "ageing money").
    credit_rows = session.execute(
        select(Payment.party_id, func.sum(PaymentAllocation.amount))
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .where(
            Payment.tenant_id == tenant_id,
            PaymentAllocation.type == AllocationType.on_account,
            Payment.status == PaymentStatus.posted,
        )
        .group_by(Payment.party_id)
    ).all()
    on_account: dict[str, Decimal] = {
        pid: Decimal(amt or 0) for pid, amt in credit_rows
    }

    party_rows = session.execute(
        select(Party.id, Party.legal_name, Party.phone, Party.opening_balance,
               Party.opening_balance_as_of, Party.created_at)
        .where(Party.tenant_id == tenant_id)
    ).all()

    @dataclass
    class _Acc:
        legal_name: str
        phone: str | None
        buckets: dict[AgeingBucket, Decimal]
        oldest_date: date | None = None
        oldest_number: int | None = None
        open_count: int = 0

    acc: dict[str, _Acc] = {}
    party_meta: dict[str, tuple] = {}
    for pid, name, phone, opening, as_of, created in party_rows:
        acc[pid] = _Acc(
            legal_name=name,
            phone=phone,
            buckets={k: _ZERO for k in _BUCKET_KEYS},
        )
        party_meta[pid] = (Decimal(opening or 0), as_of, created)

    for pid, inv_date, number, balance in inv_rows:
        a = acc.get(pid)
        if a is None:
            continue  # invoice for an archived/other-tenant party — skip
        bal = Decimal(balance or 0)
        due = inv_date + timedelta(days=credit_days)
        a.buckets[_bucket_for_age((as_on - due).days)] += bal
        a.open_count += 1
        if a.oldest_date is None or inv_date < a.oldest_date:
            a.oldest_date = inv_date
            a.oldest_number = number

    # opening debit → its own bucket by opening_balance_as_of (lt30 if null);
    # opening credit → subtract from the net later, never a bucket.
    for pid, a in acc.items():
        opening, as_of, created = party_meta[pid]
        if opening > _ZERO:
            ref = as_of or (created.date() if created is not None else as_on)
            a.buckets[_bucket_for_age((as_on - ref).days)] += opening

    out: list[AgeingRow] = []
    ql = q.lower().strip() if q else None
    for pid, a in acc.items():
        opening, _as_of, _created = party_meta[pid]
        credit = on_account.get(pid, _ZERO)
        opening_credit = -opening if opening < _ZERO else _ZERO
        bucket_total = sum(a.buckets.values(), _ZERO)
        net = bucket_total - credit - opening_credit
        if net <= _ZERO:
            continue  # net-credit or settled — not an ageing row
        if ql and ql not in a.legal_name.lower():
            continue
        worst: AgeingBucket = "lt30"
        for k in ("d90p", "d60", "d30", "lt30"):
            if a.buckets[k] > _ZERO:  # type: ignore[index]
                worst = k  # type: ignore[assignment]
                break
        out.append(
            AgeingRow(
                party_id=pid,
                legal_name=a.legal_name,
                phone=a.phone,
                lt30=a.buckets["lt30"],
                d30=a.buckets["d30"],
                d60=a.buckets["d60"],
                d90p=a.buckets["d90p"],
                total=bucket_total,
                worst_bucket=worst,
                is_overdue=worst != "lt30",
                oldest_bill_date=a.oldest_date,
                oldest_bill_number=a.oldest_number,
                last_payment_date=last_payment.get(pid),
                open_invoice_count=a.open_count,
            )
        )

    # worst first, then biggest total — the "who needs a call today" order.
    _rank = {"d90p": 0, "d60": 1, "d30": 2, "lt30": 3}
    out.sort(key=lambda r: (_rank[r.worst_bucket], -r.total))
    return out


# --------------------------------------------------------------------------
# Party ledger statement
# --------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    kind: Literal["invoice", "payment", "opening"]
    date: date
    ref_id: str
    ref_label: str
    debit: Decimal  # increases what the party owes (an invoice)
    credit: Decimal  # decreases what the party owes (a payment)
    running_balance: Decimal
    status: str
    allocations: list[dict] | None = None


def party_ledger(session: Session, party_id: str) -> list[LedgerEntry]:
    """Chronological statement: an optional opening-balance line, then
    finalized invoices (debit) + posted/reversed payments (credit, 0 for
    reversed) interleaved by date.

    Running balance is computed walking oldest -> newest (the only order in
    which "running balance" is unambiguous), then the list is reversed for
    display (newest first, bank-statement convention) — comment kept next to
    the reverse() call below so the two don't drift apart.
    """
    party = session.get(Party, party_id)
    opening = Decimal(party.opening_balance or 0) if party is not None else _ZERO

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

    running = opening
    entries: list[LedgerEntry] = []
    if opening != _ZERO:
        as_of = party.opening_balance_as_of if party is not None else None
        if as_of is None and party is not None:
            created = getattr(party, "created_at", None)
            as_of = created.date() if created is not None else date.today()
        entries.append(
            LedgerEntry(
                kind="opening",
                date=as_of or date.today(),
                ref_id=party_id,
                ref_label="Opening balance",
                debit=opening if opening > _ZERO else _ZERO,
                credit=-opening if opening < _ZERO else _ZERO,
                running_balance=running,
                status="opening",
            )
        )
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
    "has_ledger_history",
    "on_account_balance_for_party",
    "opening_balance_for_party",
    "outstanding_balance_for_party",
    "previous_outstanding_for_party",
    "open_invoices_for_party",
    "collections_summary",
    "CollectionsSummaryRow",
    "ageing_summary",
    "AgeingRow",
    "party_ledger",
    "LedgerEntry",
    "claim_voucher_no",
]

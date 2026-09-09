"""Party account statement (F3b) — a period-windowed ledger PDF, sendable
over WhatsApp.

`party_statement_data` slices `party_ledger()`-style events to a date window:
a synthetic *"Opening balance as on <start>"* row carrying everything before
the window forward, then the window's invoices + payments (reversed payments
kept, struck-through), then a **"Total due"** closing line.

Period presets: this_month | last_month | this_fy | last_90 | custom.
`this_fy` is hard-wired to the Indian Apr–Mar year.

The PDF reuses the invoice Jinja environment (money / kg / uom filters,
Indian digit grouping) and is regenerated on demand — a statement is always
"as of now", so nothing is persisted on the party.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Invoice, Party, Payment, Tenant
from app.models._mixins import InvoiceStatus, PaymentStatus
from app.services.invoices.pdf import _env  # shared Jinja env + filters

_ZERO = Decimal("0.00")

StatementPeriod = Literal["this_month", "last_month", "this_fy", "last_90", "custom"]


class StatementError(Exception):
    """Bad period arguments, or an empty window on a send."""


# --------------------------------------------------------------------------
# period resolution
# --------------------------------------------------------------------------


def _fy_start(d: date) -> date:
    """Indian financial year start (1 April) for the FY that contains `d`."""
    year = d.year if d.month >= 4 else d.year - 1
    return date(year, 4, 1)


def resolve_period(
    period: StatementPeriod,
    *,
    dt_from: date | None = None,
    dt_to: date | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """(start, end) inclusive. `custom` needs both `dt_from` and `dt_to`."""
    today = today or date.today()
    if period == "this_month":
        return date(today.year, today.month, 1), today
    if period == "last_month":
        first_this = date(today.year, today.month, 1)
        last_prev_end = first_this.fromordinal(first_this.toordinal() - 1)
        return date(last_prev_end.year, last_prev_end.month, 1), last_prev_end
    if period == "this_fy":
        return _fy_start(today), today
    if period == "last_90":
        return date.fromordinal(today.toordinal() - 89), today
    if period == "custom":
        if dt_from is None or dt_to is None:
            raise StatementError("custom period requires from and to dates")
        if dt_from > dt_to:
            raise StatementError("from date is after to date")
        return dt_from, dt_to
    raise StatementError(f"unknown period: {period!r}")


# --------------------------------------------------------------------------
# statement data
# --------------------------------------------------------------------------


@dataclass
class StatementRow:
    date: date
    particulars: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    reversed: bool = False  # a reversed payment — shown struck-through, nil effect


@dataclass
class StatementData:
    party_name: str
    party_phone: str | None
    period_from: date
    period_to: date
    generated_on: date
    opening_balance: Decimal
    rows: list[StatementRow]
    total_due: Decimal
    is_empty: bool  # no invoice / payment inside the window


def party_statement_data(
    session: Session,
    party_id: str,
    *,
    period_from: date,
    period_to: date,
    today: date | None = None,
) -> StatementData:
    today = today or date.today()
    party = session.get(Party, party_id)
    if party is None:
        raise StatementError("party not found")

    invoices = list(
        session.scalars(
            select(Invoice).where(
                Invoice.party_id == party_id,
                Invoice.status == InvoiceStatus.final,
            )
        )
    )
    payments = list(
        session.scalars(select(Payment).where(Payment.party_id == party_id))
    )

    # events as (date, tie, obj); invoices before payments on the same date.
    events: list[tuple[date, int, object]] = []
    for inv in invoices:
        events.append((inv.date, 0, inv))
    for pay in payments:
        events.append((pay.date, 1, pay))
    events.sort(key=lambda e: (e[0], e[1]))

    # opening_balance_as_of on the party is only used to date the pre-history
    # opening figure; here the statement's own opening line is dated
    # `period_from` and carries EVERYTHING before the window forward.
    opening = Decimal(party.opening_balance or 0)
    running = opening
    rows: list[StatementRow] = []
    in_window = 0

    for evt_date, _tie, obj in events:
        if isinstance(obj, Invoice):
            amt = obj.grand_total or _ZERO
            if evt_date < period_from:
                running += amt
                continue
            running += amt
            if evt_date <= period_to:
                in_window += 1
                rows.append(
                    StatementRow(
                        date=evt_date,
                        particulars=f"INV-{obj.number}" if obj.number else "INV (draft)",
                        debit=amt,
                        credit=_ZERO,
                        running_balance=running,
                    )
                )
        else:
            pay = obj
            is_reversed = pay.status == PaymentStatus.reversed
            credit_amt = _ZERO if is_reversed else pay.amount
            if evt_date < period_from:
                running -= credit_amt
                continue
            running -= credit_amt
            if evt_date <= period_to:
                in_window += 1
                label = f"Payment — {pay.mode}"
                if pay.ref_no:
                    label += f" ({pay.ref_no})"
                if is_reversed:
                    reason = pay.reversed_reason or "reversed"
                    label += f" · reversed ({reason})"
                rows.append(
                    StatementRow(
                        date=evt_date,
                        particulars=label,
                        debit=_ZERO,
                        credit=pay.amount if is_reversed else credit_amt,
                        running_balance=running,
                        reversed=is_reversed,
                    )
                )

    # opening balance for the window = running position just before the first
    # in-window event = walk the pre-window events again (cheap; already have
    # `opening` + summed above). Recompute cleanly to avoid off-by-one.
    pre = opening
    for evt_date, _tie, obj in events:
        if evt_date >= period_from:
            break
        if isinstance(obj, Invoice):
            pre += obj.grand_total or _ZERO
        else:
            pay = obj
            if pay.status != PaymentStatus.reversed:
                pre -= pay.amount

    return StatementData(
        party_name=party.legal_name,
        party_phone=party.phone,
        period_from=period_from,
        period_to=period_to,
        generated_on=today,
        opening_balance=pre,
        rows=rows,
        total_due=running,
        is_empty=in_window == 0,
    )


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def _slug(name: str) -> str:
    import re

    s = re.sub(r"[^\w]+", " ", name, flags=re.UNICODE).strip().replace(" ", "-")
    return s[:60] or "Party"


def statement_download_name(data: StatementData) -> str:
    """"<Party slug> statement <YYYY-MM-DD>.pdf" — the date is TODAY
    (generation date), matching how invoice PDFs are named, not the period.
    """
    return f"{_slug(data.party_name)} statement {data.generated_on.isoformat()}.pdf"


def render_party_statement_pdf(
    session: Session,
    party_id: str,
    *,
    period_from: date,
    period_to: date,
) -> tuple[Path, StatementData]:
    from weasyprint import HTML  # local: keep API bootable without native libs

    data = party_statement_data(
        session, party_id, period_from=period_from, period_to=period_to
    )
    party = session.get(Party, party_id)
    tenant = session.get(Tenant, party.tenant_id) if party is not None else None

    html = _env.get_template("statement_v1.html").render(tenant=tenant, s=data)

    settings = get_settings()
    out_dir = Path(settings.pdf_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"statement-{party_id}-{data.generated_on.isoformat()}.pdf"
    HTML(string=html, base_url=str(out_dir)).write_pdf(str(out_path))
    return out_path, data


__all__ = [
    "StatementPeriod",
    "StatementError",
    "resolve_period",
    "party_statement_data",
    "StatementData",
    "StatementRow",
    "render_party_statement_pdf",
    "statement_download_name",
]

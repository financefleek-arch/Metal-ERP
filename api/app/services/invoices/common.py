"""Invoice read-side helpers: FY derivation, draft totals, finalize gate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.tax import InvoiceInput, LineInput, compute_invoice
from app.domain.weighment import LineMeasure, compute_measure
from app.models import Invoice
from app.models._mixins import InvoiceStatus
from app.schemas_invoice import (
    InvoiceMeasureOut,
    InvoiceTotals,
    SegmentMeasureOut,
)

_ZERO = Decimal("0.00")


def financial_year(d: date) -> str:
    """Indian FY label for a date. Apr-Mar; `2026-04-01` -> "2026-27"."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def totals_for(inv: Invoice) -> InvoiceTotals:
    """Frozen columns for a finalized invoice; a live `compute_invoice`
    over the current lines for a draft.
    """
    if inv.status == InvoiceStatus.final:
        return InvoiceTotals(
            subtotal=inv.subtotal or _ZERO,
            discount_total=inv.discount_total or _ZERO,
            taxable_total=inv.taxable_total
            if inv.taxable_total is not None
            else (inv.subtotal or _ZERO),
            round_off=inv.round_off or _ZERO,
            grand_total=inv.grand_total or _ZERO,
            amount_in_words=inv.amount_in_words or "",
        )

    computed = compute_invoice(_input_from(inv))
    return InvoiceTotals(
        subtotal=computed.subtotal,
        discount_total=computed.discount_total,
        taxable_total=computed.taxable_total,
        round_off=computed.round_off,
        grand_total=computed.grand_total,
        amount_in_words=computed.amount_in_words,
    )


def measure_for(inv: Invoice) -> InvoiceMeasureOut:
    """Derived total weight / piece count / weighment segments. Always live
    off the current lines (qty + uom) — never stored, on a draft or a
    finalized invoice alike.
    """
    ordered = sorted(inv.lines, key=lambda x: x.sl_no)
    m = compute_measure(
        [
            LineMeasure(
                quantity=Decimal(str(ln.quantity or 0)),
                uom=ln.uom,
                segment_no=ln.segment_no or 1,
            )
            for ln in ordered
        ],
        slips=inv.weighment_slips or [],
    )
    return InvoiceMeasureOut(
        total_weight_kg=m.total_weight_kg,
        total_count=m.total_count,
        segment_count=m.segment_count,
        segments=[
            SegmentMeasureOut(
                seg=s.seg,
                line_from=s.line_from,
                line_to=s.line_to,
                weight_kg=s.weight_kg,
                count=s.count,
                recorded_kg=s.recorded_kg,
            )
            for s in m.segments
        ],
    )


def _input_from(inv: Invoice) -> InvoiceInput:
    return InvoiceInput(
        lines=[
            LineInput(
                quantity=Decimal(str(ln.quantity or 0)),
                unit_rate=Decimal(str(ln.unit_rate or 0)),
                discount=Decimal(str(ln.discount or 0)),
            )
            for ln in inv.lines
        ],
        invoice_discount=Decimal(str(inv.invoice_discount or 0)),
    )


def finalize_blockers(inv: Invoice) -> list[str]:
    """Everything that must be true before `POST /finalize` will succeed.
    Mirrors the editor's gate so the button and the API agree.
    """
    reasons: list[str] = []
    if inv.status == InvoiceStatus.final:
        reasons.append("invoice is already finalized")
        return reasons
    if inv.status == InvoiceStatus.cancelled:
        reasons.append("invoice is cancelled")
        return reasons
    if inv.party_id is None:
        reasons.append("select a party")
    real_lines = [ln for ln in inv.lines if (ln.description or "").strip()]
    if not real_lines:
        reasons.append("add at least one line with an item")
    for ln in real_lines:
        q = Decimal(str(ln.quantity or 0))
        r = Decimal(str(ln.unit_rate or 0))
        if q <= 0 or r <= 0:
            reasons.append(f"line {ln.sl_no}: needs quantity and rate")
    return reasons

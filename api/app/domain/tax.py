"""Pure invoice arithmetic — the single source of truth for money on a bill.

`compute_invoice` is called once at finalize; its result is frozen onto the
`invoice` row and never recomputed on read. The web editor carries a small
JS mirror (`web/src/lib/previewTotal.ts`) kept in lockstep by the shared
`tests/vectors/tax_vectors.json` table.

M1 is non-GST: no CGST/SGST/IGST, no HSN summary. Every figure is a
2-decimal Decimal; rounding is `ROUND_HALF_UP` (commercial rounding), done
once per boundary so paise never drift.

  line_total   = round(qty * unit_rate) - line_discount        [>= 0]
  subtotal     = sum(line_total)
  discount_tot = sum(line_discount) + invoice_discount
  taxable      = subtotal - invoice_discount                   [informational]
  round_off    = round(taxable) - taxable          in [-0.50, +0.50]
  grand_total  = taxable + round_off               (a whole rupee)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

_Q2 = Decimal("0.01")
_Q0 = Decimal("1")
_ZERO = Decimal("0.00")


def _money(value: Decimal | int | float | str) -> Decimal:
    """Coerce to a 2-decimal Decimal with commercial rounding."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineInput:
    quantity: Decimal
    unit_rate: Decimal
    discount: Decimal = _ZERO  # absolute amount off this line, not a percent


@dataclass(frozen=True)
class InvoiceInput:
    lines: list[LineInput]
    invoice_discount: Decimal = _ZERO  # absolute amount off the whole bill


@dataclass(frozen=True)
class ComputedLine:
    quantity: Decimal
    unit_rate: Decimal
    discount: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class ComputedInvoice:
    lines: list[ComputedLine] = field(default_factory=list)
    subtotal: Decimal = _ZERO
    discount_total: Decimal = _ZERO
    taxable_total: Decimal = _ZERO
    round_off: Decimal = _ZERO
    grand_total: Decimal = _ZERO
    amount_in_words: str = ""


def compute_line(line: LineInput) -> ComputedLine:
    qty = Decimal(str(line.quantity))
    rate = _money(line.unit_rate)
    disc = _money(line.discount or _ZERO)
    gross = _money(qty * rate)
    total = gross - disc
    if total < _ZERO:
        total = _ZERO
    return ComputedLine(quantity=qty, unit_rate=rate, discount=disc, line_total=total)


def compute_invoice(inp: InvoiceInput) -> ComputedInvoice:
    comp_lines = [compute_line(ln) for ln in inp.lines]

    subtotal = _money(sum((cl.line_total for cl in comp_lines), _ZERO))
    line_disc = _money(sum((cl.discount for cl in comp_lines), _ZERO))
    inv_disc = _money(inp.invoice_discount or _ZERO)

    taxable = subtotal - inv_disc
    if taxable < _ZERO:
        taxable = _ZERO

    grand = taxable.quantize(_Q0, rounding=ROUND_HALF_UP)
    round_off = _money(grand - taxable)
    grand = _money(grand)

    return ComputedInvoice(
        lines=comp_lines,
        subtotal=subtotal,
        discount_total=_money(line_disc + inv_disc),
        taxable_total=_money(taxable),
        round_off=round_off,
        grand_total=grand,
        amount_in_words=amount_in_words(grand),
    )


# --------------------------------------------------------------------------
# amount in words — Indian numbering (lakh / crore), matches the editor's JS
# --------------------------------------------------------------------------

_ONES = (
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
)
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _three(n: int) -> str:
    """0..999 -> words, with a leading 'Hundred' join."""
    hundred, rest = divmod(n, 100)
    parts: list[str] = []
    if hundred:
        parts.append(f"{_ONES[hundred]} Hundred")
    if rest:
        parts.append(_two(rest))
    return " ".join(parts)


def amount_in_words(amount: Decimal | int | float | str) -> str:
    """`1234567` -> 'INR Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Only'.

    Paise are carried as '... and NN Paise Only' when non-zero. M1 grand
    totals are whole rupees, so the paise branch is for completeness /
    intermediate display only.
    """
    amt = _money(amount)
    if amt < _ZERO:
        return "Minus " + amount_in_words(-amt)

    rupees = int(amt)
    paise = int((amt - Decimal(rupees)) * 100)

    if rupees == 0:
        words = "Zero"
    else:
        crore, rem = divmod(rupees, 10_000_000)
        lakh, rem = divmod(rem, 100_000)
        thousand, below = divmod(rem, 1000)
        chunks: list[str] = []
        if crore:
            chunks.append(f"{_two(crore)} Crore")
        if lakh:
            chunks.append(f"{_two(lakh)} Lakh")
        if thousand:
            chunks.append(f"{_two(thousand)} Thousand")
        if below:
            chunks.append(_three(below))
        words = " ".join(chunks)

    out = f"INR {words} Only"
    if paise:
        out = f"INR {words} and {_two(paise)} Paise Only"
    return out

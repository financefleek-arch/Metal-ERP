"""Derived weight & count for an invoice — display aggregates only.

Never touches money. `tax.py` still owns every rupee figure; this module
only sums the physical measures a metal-trade bill has always carried at
the bottom: total weight of weight-priced goods, and a piece count of the
rest, plus the operator-drawn weighment segments.

A line is a *weight line* when its `uom` string normalises to a mass unit
(kg / g / quintal / tonne family). Its `quantity` is converted to kg and
added to the weight total. Every other line is a *piece line* — its
`quantity` is added to the count (shown as a whole number).

The web mirror is `web/src/lib/weighment.ts`; keep the unit table and the
segment grouping identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

_Q3 = Decimal("0.001")
_ZERO = Decimal("0")

# uom (lower-cased, stripped) -> multiplier to kilograms. Anything not here
# is treated as a piece unit.
_WEIGHT_UNITS: dict[str, Decimal] = {
    "kg": Decimal("1"),
    "kgs": Decimal("1"),
    "kilogram": Decimal("1"),
    "kilograms": Decimal("1"),
    "g": Decimal("0.001"),
    "gm": Decimal("0.001"),
    "gms": Decimal("0.001"),
    "gram": Decimal("0.001"),
    "grams": Decimal("0.001"),
    "quintal": Decimal("100"),
    "qtl": Decimal("100"),
    "ton": Decimal("1000"),
    "tonne": Decimal("1000"),
    "tonnes": Decimal("1000"),
    "mt": Decimal("1000"),
}


def is_weight_uom(uom: str | None) -> bool:
    return (uom or "").strip().lower() in _WEIGHT_UNITS


def to_kg(quantity: Decimal | int | float | str, uom: str | None) -> Decimal:
    """Quantity in `uom` -> kilograms, or 0 for a non-weight unit."""
    factor = _WEIGHT_UNITS.get((uom or "").strip().lower())
    if factor is None:
        return _ZERO
    q = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity or 0))
    return (q * factor).quantize(_Q3, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineMeasure:
    quantity: Decimal
    uom: str | None
    segment_no: int = 1


@dataclass(frozen=True)
class SegmentMeasure:
    seg: int
    line_from: int  # 1-based sl_no of the first line in this segment
    line_to: int
    weight_kg: Decimal
    count: int
    # operator-recorded platform-scale weight for a closed segment, if any
    recorded_kg: Decimal | None = None


@dataclass(frozen=True)
class InvoiceMeasure:
    total_weight_kg: Decimal = _ZERO
    total_count: int = 0
    segment_count: int = 1
    segments: list[SegmentMeasure] = field(default_factory=list)


def compute_measure(
    lines: list[LineMeasure],
    slips: list[dict] | None = None,
) -> InvoiceMeasure:
    """Aggregate weight + count over the lines, grouped by `segment_no`.

    `slips` is the invoice's `weighment_slips` JSON — a list of
    `{"seg": int, "recorded_kg": str}`; the recorded figure is attached to
    the matching segment for display but never replaces the line-derived
    weight total.
    """
    if not lines:
        return InvoiceMeasure()

    recorded: dict[int, Decimal] = {}
    for s in slips or []:
        try:
            recorded[int(s["seg"])] = Decimal(str(s["recorded_kg"]))
        except (KeyError, TypeError, ValueError):
            continue

    total_w = _ZERO
    total_c = 0
    buckets: dict[int, dict] = {}
    for i, ln in enumerate(lines, start=1):
        seg = ln.segment_no or 1
        b = buckets.setdefault(
            seg, {"from": i, "to": i, "w": _ZERO, "c": 0}
        )
        b["to"] = i
        if is_weight_uom(ln.uom):
            kg = to_kg(ln.quantity, ln.uom)
            b["w"] += kg
            total_w += kg
        else:
            q = ln.quantity if isinstance(ln.quantity, Decimal) else Decimal(str(ln.quantity or 0))
            n = int(q.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            b["c"] += n
            total_c += n

    segments = [
        SegmentMeasure(
            seg=seg,
            line_from=b["from"],
            line_to=b["to"],
            weight_kg=b["w"].quantize(_Q3, rounding=ROUND_HALF_UP),
            count=b["c"],
            recorded_kg=recorded.get(seg),
        )
        for seg, b in sorted(buckets.items())
    ]

    return InvoiceMeasure(
        total_weight_kg=total_w.quantize(_Q3, rounding=ROUND_HALF_UP),
        total_count=total_c,
        segment_count=len(segments),
        segments=segments,
    )

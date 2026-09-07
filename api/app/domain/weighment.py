"""Derived weight & count for an invoice — display aggregates only.

Never touches money. `tax.py` still owns every rupee figure; this module
only sums the physical measures a metal-trade bill has always carried at
the bottom: total weight of weight-priced goods, and a piece count of the
rest, plus the operator-drawn weighment segments.

A line is a *weight line* when its `uom` is a mass unit (kg / g / quintal /
tonne family — see `api/app/domain/units.json`). Its `quantity` is converted to kg
and added to the weight total. Every other line is a *piece line* — its
`quantity` is multiplied out to individual pieces (a dozen = 12, a gross =
144; anything else = 1) and added to the count (shown as a whole number).

The unit table itself lives in `app.domain.units` (loaded from
`api/app/domain/units.json`); the web mirror is generated from the same JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.domain.units import count_multiplier, is_weight_uom, to_kg

__all__ = [
    "count_multiplier",
    "is_weight_uom",
    "to_kg",
    "LineMeasure",
    "SegmentMeasure",
    "InvoiceMeasure",
    "compute_measure",
]

_Q3 = Decimal("0.001")
_ZERO = Decimal("0")


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
            pieces = q * count_multiplier(ln.uom)
            n = int(pieces.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
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

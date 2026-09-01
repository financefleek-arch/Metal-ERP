"""Reconciliation gate — nothing downstream generates XML unless totals agree.

taxable_total + cgst_total + sgst_total + igst_total + cess + round_off,
rounded to 2dp, must equal grand_total (±0.05).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_TOLERANCE = Decimal("0.05")
_CENT = Decimal("0.01")


def _d(v: object) -> Decimal:
    if v is None:
        return Decimal(0)
    return Decimal(str(v))


@dataclass
class ReconResult:
    reconciled: bool
    expected_grand: Decimal
    actual_grand: Decimal
    discrepancy: Decimal  # actual - expected, signed


def reconcile(
    *,
    taxable_total: object,
    cgst_total: object,
    sgst_total: object,
    igst_total: object,
    round_off: object,
    grand_total: object,
    cess_total: object = None,
) -> ReconResult:
    expected = (
        _d(taxable_total)
        + _d(cgst_total)
        + _d(sgst_total)
        + _d(igst_total)
        + _d(cess_total)
        + _d(round_off)
    ).quantize(_CENT, rounding=ROUND_HALF_UP)
    actual = _d(grand_total).quantize(_CENT, rounding=ROUND_HALF_UP)
    discrepancy = actual - expected
    return ReconResult(
        reconciled=abs(discrepancy) <= _TOLERANCE,
        expected_grand=expected,
        actual_grand=actual,
        discrepancy=discrepancy,
    )

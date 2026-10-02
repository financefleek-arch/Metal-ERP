"""Catalog pricing: `sell = round(cost x multiplier / step) x step`.

Decimal end to end, half-up. The supplier price is per pack as quoted; the
multiplier is one number for the catalog with an optional per-item override.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")
_ALLOWED_STEPS = (1, 5, 10)


def sell_price(cost: Decimal, multiplier: Decimal, step: int = 1) -> Decimal:
    if step not in _ALLOWED_STEPS:
        raise ValueError(f"rounding step must be one of {_ALLOWED_STEPS}")
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    units = (cost * multiplier / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return (units * step).quantize(_CENT)


def effective_multiplier(override: Decimal | None, catalog_multiplier: Decimal) -> Decimal:
    return override if override is not None else catalog_multiplier

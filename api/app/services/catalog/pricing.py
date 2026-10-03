"""Catalog pricing: `new price = supplier price + margin %`, rounded to a step.

    new = round(cost x (100 + margin) / 100 / step) x step

The margin is a markup on the supplier's price: a 25% margin turns Rs 100 into Rs 125.
One bulk margin applies to the whole catalog, with an optional item-level margin that
takes its place for that item. Decimal end to end, half-up; a negative margin is a
discount (down to, but not including, -100%).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)
_ALLOWED_STEPS = (1, 5, 10)

MARGIN_MIN = Decimal("-99.99")
MARGIN_MAX = Decimal("1000")


def sell_price(cost: Decimal, margin_pct: Decimal, step: int = 1) -> Decimal:
    if step not in _ALLOWED_STEPS:
        raise ValueError(f"rounding step must be one of {_ALLOWED_STEPS}")
    if margin_pct <= -_HUNDRED:
        raise ValueError("margin must be above -100%")
    units = (cost * (_HUNDRED + margin_pct) / _HUNDRED / step).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    return (units * step).quantize(_CENT)


def effective_margin(item_margin: Decimal | None, bulk_margin: Decimal) -> Decimal:
    """The item's own margin when it has one, else the catalog's bulk margin."""
    return item_margin if item_margin is not None else bulk_margin

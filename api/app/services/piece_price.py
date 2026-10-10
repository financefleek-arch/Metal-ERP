"""The price of one loose piece, from the price of a pack.

One rule for the customer page, the order and the catalog PDF, so a customer never sees two
different "per piece" numbers: pack price divided by pieces in the pack, plus an optional premium
(loose pieces are more work to hand out), rounded to the paisa.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_PAISA = Decimal("0.01")


def piece_rate(
    pack_price: Decimal | float | str, pack_qty: int, premium_pct: object = 0
) -> Decimal:
    price = Decimal(str(pack_price))
    pct = Decimal(str(premium_pct or 0))
    each = price / Decimal(max(int(pack_qty or 1), 1))
    return (each * (Decimal(100) + pct) / Decimal(100)).quantize(_PAISA, rounding=ROUND_HALF_UP)

"""The unit a voucher line is sent to Tally in (S0.5).

Verified live against TallyPrime (2026-10-30, docs/GAP-REGISTER-tally-companion.md, B3): a voucher line
whose unit is not the stock item's own unit is not rejected. Tally posts it with the quantity and the
rate blank and the amount kept, so stock does not move and nothing warns. Matching is case-insensitive
(`nos` and `NOS` both work), but `pcs` against an item whose base unit is `Nos` blanks the line, and so
does `kg`.

Our canonical table folds `Pcs` into `nos`, so the unit we store cannot tell Tally's `Pcs` from its
`Nos`. Each Tally-linked item therefore keeps the exact unit name Tally holds for it (`Item.tally_unit`,
filled from a masters pull or a Check Tally). A line may go out only in that unit: when our canonical
unit for the line matches the canonical form of the item's Tally unit we send Tally's own spelling, and
otherwise the push is blocked, because we cannot convert between units.
"""

from __future__ import annotations

from app.domain.units import normalize_uom
from app.models import Item

UNKNOWN = "unknown"  # we have not seen this item's unit in Tally
DIFFERS = "differs"  # the line is in a different unit from the one Tally holds


def line_unit(item: Item, line_uom: str | None) -> tuple[str | None, str | None]:
    """(unit to send, problem). `problem` is None, UNKNOWN or DIFFERS.

    The unit is Tally's own spelling whenever it is known, so a caller that ignores the problem still
    sends a name Tally can read.
    """
    tally_unit = (item.tally_unit or "").strip()
    if not tally_unit:
        return None, UNKNOWN
    ours = normalize_uom(line_uom or item.uom or "nos")
    if ours == normalize_uom(tally_unit):
        return tally_unit, None
    return tally_unit, DIFFERS

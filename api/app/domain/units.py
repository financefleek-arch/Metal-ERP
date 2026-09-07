"""Canonical unit-of-measure table — the one place units are defined.

Loads `units.json` (this same directory) at import. Every other module
asks this one:

    normalize_uom("Doz")        -> "doz"
    is_weight_uom("kg")         -> True
    to_kg("2", "quintal")       -> Decimal("200.000")
    count_multiplier("gross")   -> 144
    is_mrp_uom("pkt")           -> True
    primary_uoms()              -> ["nos", "kg", "doz", "gross"]
    all_uoms()                  -> the wide list (secondary / purchase / legacy)

`weight_per_piece`-style pcs<->kg conversion is a separate item-level
concern and lives with the item, not here.

`units.json` sits next to this module deliberately: the API Docker build
context is `api/` only, so a repo-root file would not ship in the
container (this bit us live 2026-09-07 — /shared/units.json FileNotFound,
cascading 500s). The web app generates web/src/lib/units.generated.ts
from this same file; `test_units.py` asserts the two never drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

_Q3 = Decimal("0.001")
_ZERO = Decimal("0")

_JSON_PATH = Path(__file__).resolve().parent / "units.json"


@dataclass(frozen=True)
class Unit:
    value: str
    label: str
    kind: str  # "weight" | "count"
    kg_factor: Decimal | None
    piece_multiplier: Decimal | None
    is_mrp: bool
    primary: bool
    aliases: tuple[str, ...]


@lru_cache(maxsize=1)
def _load() -> tuple[list[Unit], dict[str, Unit]]:
    raw = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    units: list[Unit] = []
    lookup: dict[str, Unit] = {}
    for row in raw["units"]:
        u = Unit(
            value=row["value"],
            label=row["label"],
            kind=row["kind"],
            kg_factor=(
                Decimal(str(row["kg_factor"])) if row["kg_factor"] is not None else None
            ),
            piece_multiplier=(
                Decimal(str(row["piece_multiplier"]))
                if row["piece_multiplier"] is not None
                else None
            ),
            is_mrp=bool(row["is_mrp"]),
            primary=bool(row["primary"]),
            aliases=tuple(a.lower() for a in row.get("aliases", [])),
        )
        units.append(u)
        lookup[u.value.lower()] = u
        for a in u.aliases:
            lookup.setdefault(a, u)
    return units, lookup


def _all() -> list[Unit]:
    return _load()[0]


def _by_key(uom: str | None) -> Unit | None:
    if not uom:
        return None
    return _load()[1].get(uom.strip().lower())


# --------------------------------------------------------------------------
# public surface
# --------------------------------------------------------------------------


def normalize_uom(uom: str | None) -> str:
    """Fold a raw unit string to its canonical value. Unknown -> lowercased
    passthrough (a real but un-tabled unit is left recognisable, not blanked)."""
    if not uom or not uom.strip():
        return ""
    key = uom.strip().lower()
    hit = _by_key(key)
    return hit.value if hit is not None else key


def is_weight_uom(uom: str | None) -> bool:
    u = _by_key(uom)
    return u is not None and u.kind == "weight"


def is_mrp_uom(uom: str | None) -> bool:
    """Discrete/boxed good -> item_type hint = mrp. Unknown units are bulk."""
    u = _by_key(uom)
    return u is not None and u.is_mrp


def count_multiplier(uom: str | None) -> Decimal:
    """Individual pieces in one `uom` (dozen -> 12, gross -> 144). Weight units
    and unknowns -> 1."""
    u = _by_key(uom)
    if u is None or u.piece_multiplier is None:
        return Decimal("1")
    return u.piece_multiplier


def to_kg(quantity: Decimal | int | float | str, uom: str | None) -> Decimal:
    """Quantity in `uom` -> kilograms, or 0 for a non-weight unit."""
    u = _by_key(uom)
    if u is None or u.kg_factor is None:
        return _ZERO
    q = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity or 0))
    return (q * u.kg_factor).quantize(_Q3, rounding=ROUND_HALF_UP)


def primary_uoms() -> list[str]:
    """The strict billing set — invoice line + an item's primary unit."""
    return [u.value for u in _all() if u.primary]


def all_uoms() -> list[str]:
    """The wide list — an item's secondary / purchase unit, and legacy data."""
    return [u.value for u in _all()]


def unit_labels() -> dict[str, str]:
    return {u.value: u.label for u in _all()}

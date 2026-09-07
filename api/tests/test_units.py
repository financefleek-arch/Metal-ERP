"""The canonical unit table (`shared/units.json`) and its two consumers.

`app.domain.units` loads the JSON; `web/src/lib/units.generated.ts` is
generated from the same file. This asserts:
  1. the Python surface behaves (normalise / weight / count / to_kg / mrp)
  2. the generated TS file is in lockstep with the JSON — if someone edits
     units.json and forgets `node scripts/gen-units.mjs`, this fails.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from app.domain.units import (
    _JSON_PATH,
    all_uoms,
    count_multiplier,
    is_mrp_uom,
    is_weight_uom,
    normalize_uom,
    primary_uoms,
    to_kg,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED_TS = _REPO_ROOT / "web" / "src" / "lib" / "units.generated.ts"


# --------------------------------------------------------------------------
# python surface
# --------------------------------------------------------------------------


def test_primary_set() -> None:
    assert primary_uoms() == ["nos", "kg", "doz", "gross"]


def test_normalise_folds_aliases_and_case() -> None:
    assert normalize_uom("Doz") == "doz"
    assert normalize_uom(" KGS ") == "kg"
    assert normalize_uom("PCS") == "nos"
    assert normalize_uom("Mtr") == "m"
    assert normalize_uom("PKT") == "pkt"
    # unknown -> lowercased passthrough, not blanked
    assert normalize_uom("Widget") == "widget"
    assert normalize_uom("") == ""
    assert normalize_uom(None) == ""


def test_weight_units() -> None:
    assert is_weight_uom("kg") and is_weight_uom("QUINTAL") and is_weight_uom("mt")
    assert not is_weight_uom("nos") and not is_weight_uom("doz") and not is_weight_uom(None)
    assert to_kg("2", "quintal") == Decimal("200.000")
    assert to_kg("500", "g") == Decimal("0.500")
    assert to_kg("3", "nos") == Decimal("0")


def test_count_multipliers() -> None:
    assert count_multiplier("nos") == 1
    assert count_multiplier("doz") == 12
    assert count_multiplier("DOZEN") == 12
    assert count_multiplier("gross") == 144
    assert count_multiplier("pair") == 2
    assert count_multiplier("bundle") == 1  # count unit, no multiple
    assert count_multiplier("kg") == 1      # weight unit -> 1
    assert count_multiplier("unknown") == 1


def test_mrp_hint() -> None:
    assert is_mrp_uom("nos") and is_mrp_uom("doz") and is_mrp_uom("pkt")
    assert not is_mrp_uom("kg") and not is_mrp_uom("bundle") and not is_mrp_uom(None)


def test_every_primary_is_in_the_wide_list() -> None:
    assert set(primary_uoms()) <= set(all_uoms())


# --------------------------------------------------------------------------
# py <-> ts lockstep
# --------------------------------------------------------------------------


def _ts_units() -> list[dict]:
    """Parse the `UNITS: Unit[] = [ ... ];` array literal out of the generated
    file. It's emitted by `JSON.stringify`, so it's valid JSON once isolated."""
    text = _GENERATED_TS.read_text(encoding="utf-8")
    m = re.search(r"export const UNITS: Unit\[\] = (\[.*?\]);", text, re.DOTALL)
    assert m, "UNITS array not found in units.generated.ts"
    return json.loads(m.group(1))


def test_generated_ts_matches_json() -> None:
    src = json.loads(_JSON_PATH.read_text(encoding="utf-8"))["units"]
    ts = _ts_units()

    assert [u["value"] for u in ts] == [u["value"] for u in src], (
        "units.generated.ts is stale — run `node scripts/gen-units.mjs` in web/"
    )
    for s, t in zip(src, ts, strict=True):
        assert t == {
            "value": s["value"],
            "label": s["label"],
            "kind": s["kind"],
            "kgFactor": s["kg_factor"],
            "pieceMultiplier": s["piece_multiplier"],
            "isMrp": s["is_mrp"],
            "primary": s["primary"],
            "aliases": s.get("aliases", []),
        }, f"row for {s['value']!r} differs between JSON and generated TS"


def test_generated_ts_exists_and_has_the_helpers() -> None:
    text = _GENERATED_TS.read_text(encoding="utf-8")
    for fn in ("normalizeUom", "isWeightUom", "countMultiplier", "toKg", "PRIMARY_UOMS"):
        assert fn in text, f"{fn} missing from units.generated.ts"

"""S0.5 - which unit a voucher line goes to Tally in (services/tally/units.py).

Live finding (2026-10-30, docs/GAP-REGISTER-tally-companion.md B3): a line in any unit other than the item's
own posts with a blank quantity and rate. `nos`/`NOS` match `Nos`, but `pcs` and `kg` do not.
"""

from __future__ import annotations

from app.models import Item
from app.services.tally.units import DIFFERS, UNKNOWN, line_unit


def _item(tally_unit: str | None, uom: str | None = "nos") -> Item:
    return Item(id="i1", tenant_id="t1", name="X", name_normalized="x", uom=uom, tally_unit=tally_unit)


def test_unknown_when_tally_unit_was_never_seen() -> None:
    assert line_unit(_item(None), "nos") == (None, UNKNOWN)
    assert line_unit(_item("  "), "nos") == (None, UNKNOWN)


def test_same_unit_in_any_case_goes_out_in_tallys_spelling() -> None:
    assert line_unit(_item("Nos"), "nos") == ("Nos", None)
    assert line_unit(_item("Nos"), "NOS") == ("Nos", None)


def test_pcs_and_nos_are_one_unit_for_us_but_tally_keeps_its_own_name() -> None:
    assert line_unit(_item("Pcs"), "nos") == ("Pcs", None)
    assert line_unit(_item("Nos"), "pcs") == ("Nos", None)


def test_weight_alias_matches() -> None:
    assert line_unit(_item("Kgs"), "kg") == ("Kgs", None)


def test_a_different_unit_is_a_mismatch_and_still_names_tallys_unit() -> None:
    assert line_unit(_item("Nos"), "kg") == ("Nos", DIFFERS)
    assert line_unit(_item("Kgs"), "nos") == ("Kgs", DIFFERS)


def test_a_line_with_no_unit_uses_the_items_own() -> None:
    assert line_unit(_item("Kgs", uom="kg"), None) == ("Kgs", None)
    assert line_unit(_item("Kgs", uom=None), None) == ("Kgs", DIFFERS)  # defaults to nos

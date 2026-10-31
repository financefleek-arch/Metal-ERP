"""S0.5 - which unit a voucher line goes to Tally in (services/tally/units.py).

Live finding (2026-10-30, docs/GAP-REGISTER-tally-companion.md B3): a line in any unit other than the item's
own posts with a blank quantity and rate. `nos`/`NOS` match `Nos`, but `pcs` and `kg` do not.
"""

from __future__ import annotations

from app.services.tally.units import DIFFERS, UNKNOWN, line_unit


def LU(tally_unit: str | None, line_uom: str | None, uom: str | None = "nos"):  # noqa: N802
    """`line_unit` for an item whose own unit is `uom` and which the company holds as `tally_unit`."""
    return line_unit(tally_unit, line_uom, uom)


def test_unknown_when_tally_unit_was_never_seen() -> None:
    assert LU(None, "nos") == (None, UNKNOWN)
    assert LU("  ", "nos") == (None, UNKNOWN)


def test_same_unit_in_any_case_goes_out_in_tallys_spelling() -> None:
    assert LU("Nos", "nos") == ("Nos", None)
    assert LU("Nos", "NOS") == ("Nos", None)


def test_pcs_and_nos_are_one_unit_for_us_but_tally_keeps_its_own_name() -> None:
    assert LU("Pcs", "nos") == ("Pcs", None)
    assert LU("Nos", "pcs") == ("Nos", None)


def test_weight_alias_matches() -> None:
    assert LU("Kgs", "kg") == ("Kgs", None)


def test_a_different_unit_is_a_mismatch_and_still_names_tallys_unit() -> None:
    assert LU("Nos", "kg") == ("Nos", DIFFERS)
    assert LU("Kgs", "nos") == ("Kgs", DIFFERS)


def test_a_line_with_no_unit_uses_the_items_own() -> None:
    assert LU("Kgs", None, "kg") == ("Kgs", None)
    assert LU("Kgs", None, None) == ("Kgs", DIFFERS)  # defaults to nos

"""S1.9 - a Tally pull never overwrites what the shop maintains (services/tally/ownership.py)."""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Item, Party
from app.services.tally.ownership import (
    ITEM_FILLED_FROM_TALLY,
    ITEM_OURS,
    PARTY_FILLED_FROM_TALLY,
    PARTY_OURS,
    conflict,
)

_ROUTERS = Path(__file__).resolve().parent.parent / "app" / "routers"


def _commit_source(name: str) -> str:
    text = (_ROUTERS / name).read_text(encoding="utf-8")
    return text[text.index("def commit("):]


def test_the_two_sides_do_not_overlap_and_name_real_columns() -> None:
    assert not PARTY_FILLED_FROM_TALLY & PARTY_OURS
    assert not ITEM_FILLED_FROM_TALLY & ITEM_OURS
    party_cols = set(Party.__mapper__.attrs.keys())
    item_cols = set(Item.__mapper__.attrs.keys())
    assert PARTY_FILLED_FROM_TALLY <= party_cols, PARTY_FILLED_FROM_TALLY - party_cols
    assert ITEM_FILLED_FROM_TALLY <= item_cols, ITEM_FILLED_FROM_TALLY - item_cols


def test_the_party_importer_only_fills_listed_fields() -> None:
    src = _commit_source("parties_import.py")
    written = set(re.findall(r'_fill_blank\(party, "(\w+)"', src))
    written |= set(re.findall(r"\bparty\.(\w+)\s*=(?!=)", src))
    written |= set(re.findall(r"\bparty\.(\w+)\.append\(", src))
    assert written, "the scan found nothing: update this test with the importer"
    assert written <= PARTY_FILLED_FROM_TALLY, written - PARTY_FILLED_FROM_TALLY
    assert not written & PARTY_OURS


def test_the_item_importer_only_fills_listed_fields() -> None:
    src = _commit_source("items_import.py")
    written = set(re.findall(r"\bit\.(\w+)\s*=(?!=)", src))
    assert written, "the scan found nothing: update this test with the importer"
    assert written <= ITEM_FILLED_FROM_TALLY, written - ITEM_FILLED_FROM_TALLY
    assert not written & ITEM_OURS


def test_a_conflict_is_only_a_real_difference() -> None:
    assert conflict("A", "gstin", "27AAA", "27AAA") is None
    assert conflict("A", "gstin", "27aaa", "27AAA") is None  # case only
    assert conflict("A", "gstin", "", "27AAA") is None  # ours blank: it will be filled
    assert conflict("A", "gstin", "27AAA", "") is None
    found = conflict("A", "gstin", "27AAA", "29BBB")
    assert found is not None and (found.ours, found.tally) == ("27AAA", "29BBB")

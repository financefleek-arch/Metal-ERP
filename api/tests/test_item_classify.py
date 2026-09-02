"""The rules-first item classifier: golden fixture coverage + a hand-labelled
set of one item per department, plus the brand / HSN-fallback / Other paths.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.domain.item_classify import LearnedRule, classify_item
from app.domain.item_taxonomy import (
    DEPARTMENTS,
    OTHER_DEPARTMENT,
    all_group_names,
)

_GOLDEN = Path(__file__).parent / "fixtures" / "sethia_classify_golden.csv"


# --------------------------------------------------------------------------
# taxonomy self-consistency
# --------------------------------------------------------------------------


def test_taxonomy_is_self_consistent() -> None:
    pairs = set(all_group_names())
    depts = set(DEPARTMENTS)
    for dept, _grp in pairs:
        assert dept in depts, f"group department not in DEPARTMENTS: {dept}"
    # every classifier result names a (dept, group) the seed will have created
    from app.domain.item_taxonomy import HSN_FALLBACK_GROUP, RULES

    for dept, grp, _ph in RULES:
        assert (dept, grp) in pairs
    for dept, grp in HSN_FALLBACK_GROUP.items():
        assert (dept, grp) in pairs


# --------------------------------------------------------------------------
# hand-labelled: one real item per department
# --------------------------------------------------------------------------

_LABELLED: list[tuple[str, str, str, str]] = [
    # name, hsn, expected department, expected group
    ("10 G.I. BUCKET", "73239390", "Household & Cleaning", "Bucket - GI / Steel"),
    ("240 MM KADAI GRANITE", "76151030", "Cookware", "Kadai / Kadhai"),
    ("BSS 16CM SKIMMER STAINLESS HANDLE", "82159900",
     "Cutlery & Kitchen Tools", "Skimmer / Jhara / Palta"),
    ("1.8L RICE COOKER SR-WA18H (SS)", "85166000",
     "Kitchen Appliances", "Rice Cooker"),
    ("AL PRESSURE COOKER 5 LTR", "76151030", "Pressure Cookers", "Pressure Cooker"),
    ("BABY GASKIT PCS", "40169340", "Pressure Cookers", "Cooker Gasket"),
    ("03 DIL MINO JHULA", "4421", "Pooja & Wooden Goods", "Jhula / Palna"),
    ("6*10 GOLD CLOSED MANDIR", "4421", "Pooja & Wooden Goods", "Mandir / Temple"),
    ("15068 THERMINOX STARK 1000ML", "96170011",
     "Flasks, Bottles & Thermoware", "Vacuum Flask / Thermos"),
    ("1 Kg Jar", "70134900", "Glassware & Crockery", "Storage Jar"),
    ("CHAIR WITHOUT ARM", "94037000", "Furniture", "Chair"),
    ("CHOPPING BOARD", "39241010", "Plasticware", "Chopping Board"),
    ("CELLO DINNER SET 13P", "73239390",
     "Steel Utensils & Serveware", "Dinner Set"),
]


@pytest.mark.parametrize("name,hsn,dept,grp", _LABELLED)
def test_labelled_items(name: str, hsn: str, dept: str, grp: str) -> None:
    r = classify_item(name, hsn=hsn)
    assert (r.department, r.group) == (dept, grp), r


# --------------------------------------------------------------------------
# brand / hybrid
# --------------------------------------------------------------------------


def test_brand_is_detected_and_confidence_bumped() -> None:
    r = classify_item("HAWKINS CLASSIC 10L", hsn="73239310")
    assert r.brand == "Hawkins"
    assert r.department == "Pressure Cookers"
    assert r.confidence >= 0.9  # branded keyword hit

    plain = classify_item("AL PRESSURE COOKER 10 LTR", hsn="76151030")
    assert plain.brand is None
    assert plain.confidence < r.confidence


# --------------------------------------------------------------------------
# HSN fallback + Other
# --------------------------------------------------------------------------


def test_hsn_chapter_fallback_when_no_keyword() -> None:
    # a model-name-only string, no product word — chapter 73 -> steel serveware
    r = classify_item("A.C STEAM 2500 ML", hsn="73239390")
    assert r.source == "hsn"
    assert r.department == "Steel Utensils & Serveware"
    assert 0.3 <= r.confidence < 0.7  # assignable, not auto-confirmed


def test_no_keyword_no_hsn_is_other() -> None:
    r = classify_item("SOME MYSTERY WIDGET", hsn=None)
    assert r.department == OTHER_DEPARTMENT
    assert r.source == "none"
    assert r.confidence == 0.0


def test_blank_name_is_other() -> None:
    r = classify_item("   ", hsn="73239390")
    assert r.department == OTHER_DEPARTMENT


# --------------------------------------------------------------------------
# learned rules win over the seed table
# --------------------------------------------------------------------------


def test_learned_rule_overrides_seed() -> None:
    # "balti" alone would hit Household/Bucket via the seed table
    learned = [LearnedRule(phrase="balti", department="Cookware", group="Handi")]
    r = classify_item("STEEL BALTI 5", learned=learned)
    assert (r.department, r.group) == ("Cookware", "Handi")
    assert r.source == "learned"


# --------------------------------------------------------------------------
# golden fixture: aggregate coverage must not regress
# --------------------------------------------------------------------------


def _load_golden() -> list[dict]:
    with _GOLDEN.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_golden_fixture_present() -> None:
    rows = _load_golden()
    assert len(rows) == 2094


def test_golden_coverage_holds() -> None:
    rows = _load_golden()
    n = len(rows)
    other = sum(1 for r in rows if r["department"] == OTHER_DEPARTMENT)
    confirm = sum(1 for r in rows if float(r["confidence"]) >= 0.70)
    # >= 95% land in a real department; >= 55% auto-confirm
    assert other / n <= 0.05, f"{other}/{n} in Other"
    assert confirm / n >= 0.55, f"only {confirm}/{n} would auto-confirm"


def test_golden_matches_current_classifier() -> None:
    """Every row's live classification still equals what the fixture recorded
    — regression guard for taxonomy / rule edits. Regenerate the CSV
    deliberately when a change is intended:
        python -m tools.reclassify_items --tenant <id> --out ...
    (or the snippet in the fixture's header).
    """
    mismatches: list[str] = []
    for r in _load_golden():
        live = classify_item(r["name"], hsn=r["hsn"] or None, uom=r["uom"] or None)
        if (live.department, live.group) != (r["department"], r["group"]):
            mismatches.append(
                f"{r['name']!r}: fixture {r['department']}/{r['group']} "
                f"-> live {live.department}/{live.group}"
            )
    assert not mismatches, "\n".join(mismatches[:20])

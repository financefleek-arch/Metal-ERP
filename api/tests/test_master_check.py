"""S2.7 - a purchase push does not create what the company's Tally already has (services/tally/master_check.py)."""

# ruff: noqa: F811
from __future__ import annotations

from app.models import TallyCompany
from app.services.tally.master_check import classify, known_item_names, known_supplier_names


def _company() -> TallyCompany:
    return TallyCompany(
        tenant_id="t", company_name="C", ledger_map={},
        known_ledgers=[
            {"name": "Sugal Glass House", "parent": "Sundry Creditors", "kind": "ledger"},
            {"name": "Sundry Creditors", "parent": None, "kind": "group"},
        ],
        known_stock_items=["ss thali 11in", "steel glass 250ml"],
    )


def test_only_ledgers_count_as_suppliers_not_groups() -> None:
    assert list(known_supplier_names(_company())) == ["sugal glass house"]


def test_exact_near_and_new() -> None:
    c = _company()
    suppliers, items = known_supplier_names(c), known_item_names(c)
    assert classify("SUGAL  glass house", suppliers) == ("exact", "Sugal Glass House")  # case, spaces
    kind, theirs = classify("Sugal Glass Hous", suppliers)
    assert kind == "near" and theirs == "Sugal Glass House"
    assert classify("Ramesh Metals", suppliers) == ("new", None)
    assert classify("SS Thali 11 in", items)[0] == "near"
    assert classify("Brass Lota", items) == ("new", None)
    assert classify("anything", {}) == ("new", None)  # nothing known yet: create as before

"""The id-keyed light queries and what the refill does with them (light_masters.py + refill.py),
against captures from a live TallyPrime 7.1."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ItemTallyState, PartyTallySnapshot, TallyLink
from app.models._mixins import PartyRole
from app.services.tally.companies import add_company
from app.services.tally.light_masters import (
    LedgerRow,
    parse_groups,
    parse_ledgers,
    parse_stock_items,
    party_tree,
)
from app.services.tally.links import record_master_guid
from app.services.tally.refill import apply_outstanding, apply_stock, balances_from_ledgers
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_tally_links import _item, _party

FIX = Path(__file__).parent / "fixtures"
D = Decimal
TODAY = date(2026, 10, 31)
CUSTOMER_GUID = "167b31a2-6486-4396-8ee8-7fe38496052d-000000db"  # ZZTEST Customer in the capture
SUPPLIER_GUID = "167b31a2-6486-4396-8ee8-7fe38496052d-000000e2"  # ZZTEST Purchase Supplier
ITEM_GUID = "167b31a2-6486-4396-8ee8-7fe38496052d-000000d4"  # Monin Blueberry Syrup 700Mlx6


def _ledgers():
    return parse_ledgers((FIX / "tally_ledgers_dev_sample.xml").read_bytes())


def _items():
    return parse_stock_items((FIX / "tally_stock_items_dev_sample.xml").read_bytes())


def _firm(client: TestClient, email: str) -> str:
    return _register_firm(client, email)[1]


# ------------------------------------------------------------------------------ the parsers


def test_ledgers_carry_id_group_and_the_sign_tally_writes() -> None:
    rows = {r.name: r for r in _ledgers()}
    assert len(rows) == 12  # the <LEDGER>0</LEDGER> counter in the file header is not a row
    cust = rows["ZZTEST Customer"]
    tree = party_tree(parse_groups((FIX / "tally_groups_dev_sample.xml").read_bytes()))
    assert cust.guid == CUSTOMER_GUID and cust.group == "Sundry Debtors"
    assert tree.role_for(cust.group) == PartyRole.customer
    assert cust.closing == D("-5292.40") and cust.owed_to_firm == D("5292.40")
    sup = rows["ZZTEST Purchase Supplier"]
    assert sup.group == "Sundry Creditors" and sup.owed_to_firm == D("-9260.30")  # we owe them
    assert tree.role_for(sup.group) == PartyRole.supplier
    assert tree.role_for(rows["ZZTEST Sales"].group) is None and tree.role_for(rows["Cash"].group) is None
    assert rows["Profit & Loss A/c"].group == "Primary"  # the stray control character is dropped


def test_stock_items_carry_id_unit_quantity_value_and_rate() -> None:
    rows = {r.name: r for r in _items()}
    syrup = rows["Monin Blueberry Syrup 700Mlx6"]
    assert syrup.guid == ITEM_GUID and syrup.unit == "Pcs" and syrup.group == "Primary"
    assert syrup.qty == D(2) and syrup.qty_unit == "Pcs"
    assert syrup.rate == D("570.11") and syrup.value == D("1140.22")  # Tally writes -1140.22
    empty = next(r for r in rows.values() if r.qty is None)
    assert empty.value is None and empty.guid


def test_the_group_list_reads_with_parents_and_the_stray_control_character_dropped() -> None:
    groups = {g.name: g.parent for g in parse_groups((FIX / "tally_groups_dev_sample.xml").read_bytes())}
    assert len(groups) == 28
    assert groups["Sundry Debtors"] == "Current Assets" and groups["Sales Accounts"] == "Primary"


def test_customers_under_a_shops_own_groups_are_found_through_the_lineage() -> None:
    """Positive's customers sit under AASAM, SILIGURI and so on, which sit under Sundry Debtors."""
    from tools.tally_import.parser import TallyGroup

    groups = [
        TallyGroup("Sundry Debtors", "Current Assets"),
        TallyGroup("SILIGURI", "Sundry Debtors"),
        TallyGroup("SILIGURI B M", "SILIGURI"),
        TallyGroup("Sundry Creditors", "Current Liabilities"),
    ]
    rows = [
        LedgerRow("g1", "Anand", "SILIGURI B M", D("-100"), None),
        LedgerRow("g2", "Cash", "Cash-in-Hand", D("5"), None),
        LedgerRow("g3", "Our Supplier", "Sundry Creditors", D("40"), None),
    ]
    from app.services.tally.refill import balances_from_ledgers as bl

    cands = {b.name: b.candidate for b in bl(rows, groups)}
    assert cands == {"Anand": True, "Cash": False, "Our Supplier": True}
    # with no group list at all only the two root names are recognised: the reason the chain reads it first
    assert {b.name: b.candidate for b in bl(rows)}["Anand"] is False


# -------------------------------------------------------------------- matching by Tally's id


def test_stock_is_matched_by_id_even_when_the_names_differ(client: TestClient) -> None:
    tid = _firm(client, "lm1@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        it = _item(s, tid, "Our Own Name For Syrup")
        record_master_guid(s, tid, c, "item", it, ITEM_GUID, name="Old Tally Spelling")
        res = apply_stock(s, c, _items())
        s.commit()
        assert res.matched == 1 and res.renamed == 1
        st = s.scalar(select(ItemTallyState))
        assert st.item_id == it.id and st.stock_qty == D(2) and st.stock_value == D("1140.22")
        link = s.scalar(select(TallyLink).where(TallyLink.entity_id == it.id))
        assert link.tally_name == "Monin Blueberry Syrup 700Mlx6"  # corrected, nobody asked


def test_a_second_refresh_with_the_same_names_changes_no_names(client: TestClient) -> None:
    tid = _firm(client, "lm2@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        it = _item(s, tid, "Syrup")
        record_master_guid(s, tid, c, "item", it, ITEM_GUID, name="Monin Blueberry Syrup 700Mlx6")
        assert apply_stock(s, c, _items()).renamed == 0


def test_items_in_tally_that_metal_does_not_hold_are_listed_not_created(client: TestClient) -> None:
    tid = _firm(client, "lm3@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        res = apply_stock(s, c, _items())
        assert res.matched == 0 and len(res.unmatched) == len(_items())
        assert s.scalar(select(ItemTallyState)) is None


def test_a_customer_with_no_history_gets_tallys_balance_as_the_opening_balance(
    client: TestClient,
) -> None:
    tid = _firm(client, "lm4@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "Zz Customer, as we spell it")
        record_master_guid(s, tid, c, "party", p, CUSTOMER_GUID, name="ZZTEST Customer")
        res = apply_outstanding(
            s, c, balances_from_ledgers(_ledgers()), TODAY, absent_is_zero=False
        )
        s.commit()
        s.refresh(p)
        assert p.opening_balance == D("5292.40") and p.opening_balance_as_of == TODAY
        assert res.opening_set == 1
        snap = s.scalar(select(PartyTallySnapshot))
        assert snap.party_id == p.id and snap.outstanding == D("5292.40")
        # every other ledger in the file is Cash, expenses and so on: none is reported as a missing party
        assert set(res.unmatched) == {
            "SUGAL FOODS",
            "ZZTEST Purchase Supplier",
            "ZZTEST Purchase Supplier F5EF",
        }


def test_a_supplier_gets_a_snapshot_but_no_opening_balance(client: TestClient) -> None:
    tid = _firm(client, "lm5@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        sup = _party(s, tid, "Our Supplier")
        sup.role = PartyRole.supplier
        record_master_guid(s, tid, c, "party", sup, SUPPLIER_GUID, name="ZZTEST Purchase Supplier")
        res = apply_outstanding(
            s, c, balances_from_ledgers(_ledgers()), TODAY, absent_is_zero=False
        )
        s.refresh(sup)
        assert (sup.opening_balance or 0) == 0 and res.suppliers == 1
        assert s.scalar(select(PartyTallySnapshot.outstanding)) == D("-9260.30")


def test_a_full_ledger_list_does_not_zero_a_party_it_does_not_mention(client: TestClient) -> None:
    tid = _firm(client, "lm6@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        gone = _party(s, tid, "Gone From Tally")
        record_master_guid(s, tid, c, "party", gone, "guid-not-in-the-file", name="Gone From Tally")
        apply_outstanding(s, c, balances_from_ledgers(_ledgers()), TODAY, absent_is_zero=False)
        assert s.scalar(select(PartyTallySnapshot)) is None  # no balance invented for it


def test_voucher_types_read_with_their_parents() -> None:
    from app.services.tally.light_masters import parse_voucher_types

    types = parse_voucher_types((FIX / "tally_voucher_types_dev_sample.xml").read_bytes())
    assert len(types) == 24 and types["Sales"] == "Sales" and types["Sales Order"] == "Sales Order"
    assert types["Receipt"] == "Receipt" and types["Credit Note"] == "Credit Note"


"""The refill: stock on hand, what parties owe, last sale and last rate per party and item
(services/tally/refill.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ItemTallyState, PartyItemPrice, PartyTallySnapshot
from app.services.tally import refill
from app.services.tally.companies import add_company
from app.services.tally.links import record_master_guid
from app.services.tally.light_masters import ItemRow
from app.services.tally.refill import (
    apply_outstanding,
    apply_sales,
    apply_stock,
    balances_from_bills,
    items_from_stock_summary,
)
from app.services.tally.stock_summary import parse_stock_summary
from app.services.tally.voucher_xml import PendingBill, parse_vouchers
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_tally_links import _item, _party

FIX = Path(__file__).parent / "fixtures"
D = Decimal


def _firm(client: TestClient, email: str) -> str:
    return _register_firm(client, email)[1]


def _row(name: str, qty: str | None = "5", unit: str | None = "Pcs", guid: str = "") -> ItemRow:
    return ItemRow(
        guid=guid,
        name=name,
        group="",
        unit=unit,
        qty=D(qty) if qty is not None else None,
        qty_unit=unit,
        rate=D("10.00"),
        value=D("50.00"),
    )


# -------------------------------------------------------------------------------------- stock


def test_stock_lands_on_the_matched_item_in_that_company_only(client: TestClient) -> None:
    tid = _firm(client, "rf1@x.example.com")
    with SessionLocal() as s:
        gst, est = add_company(s, tid, "GST"), add_company(s, tid, "Estimate", kind="estimate")
        it = _item(s, tid, "SS Thali")
        res = apply_stock(s, gst, [_row("ss  THALI", "19"), _row("Mystery")])
        s.commit()
        assert (res.matched, res.unmatched) == (1, ["Mystery"])
        st = s.scalar(select(ItemTallyState).where(ItemTallyState.company_id == gst.id))
        assert st.item_id == it.id and st.stock_qty == D("19") and st.stock_unit == "Pcs"
        assert st.stock_value == D("50.00") and st.status == "none"
        assert s.scalar(select(ItemTallyState).where(ItemTallyState.company_id == est.id)) is None


def test_stock_matches_the_name_tally_uses_even_after_we_rename(client: TestClient) -> None:
    tid = _firm(client, "rf2@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        it = _item(s, tid, "Our New Name")
        record_master_guid(s, tid, c, "item", it, "g-1", name="Old Tally Name")
        res = apply_stock(s, c, [_row("Old Tally Name")])
        assert res.matched == 1 and res.unmatched == []


def test_an_ambiguous_name_is_not_guessed(client: TestClient) -> None:
    tid = _firm(client, "rf3@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        a, b = _item(s, tid, "Item A"), _item(s, tid, "Item B")
        record_master_guid(s, tid, c, "item", a, "g-a", name="Dup")
        record_master_guid(s, tid, c, "item", b, "g-b", name="Dup")  # two records, one Tally name
        res = apply_stock(s, c, [_row("Dup")])
        assert res.matched == 0 and res.unmatched == ["Dup"]


def test_adjustment_lines_and_oversold_stock_are_counted(client: TestClient) -> None:
    tid = _firm(client, "rf4@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _item(s, tid, "Cooker")
        rows = [_row("Cooker", "-188"), _row("Rate Difference", None)]
        res = apply_stock(s, c, rows)
        assert (res.matched, res.negative, res.adjustment_lines) == (1, 1, 1)
        assert s.scalar(select(ItemTallyState.stock_qty)) == D("-188")


def test_a_second_refresh_overwrites_not_duplicates(client: TestClient) -> None:
    tid = _firm(client, "rf5@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _item(s, tid, "Thali")
        apply_stock(s, c, [_row("Thali", "5")])
        apply_stock(s, c, [_row("Thali", "9")])
        rows = s.scalars(select(ItemTallyState)).all()
        assert len(rows) == 1 and rows[0].stock_qty == D("9")


def test_the_real_stock_summary_export_reads_end_to_end(client: TestClient) -> None:
    tid = _firm(client, "rf6@x.example.com")
    summary = parse_stock_summary((FIX / "tally_stock_summary_sample.xml").read_bytes())
    rows = items_from_stock_summary(summary)  # a UI export has no ids: matched by name
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _item(s, tid, "1/3 LID")
        res = apply_stock(s, c, rows)
        assert res.matched == 1 and res.total == len(rows) and res.adjustment_lines == 0


# ---------------------------------------------------------------------- what parties owe


def _bill(party: str, closing: str, day: date = date(2026, 9, 2)) -> PendingBill:
    return PendingBill(ref="B", party=party, day=day, due=day, closing=D(closing))


def test_tallys_outstanding_becomes_the_opening_balance_for_a_party_with_no_history(
    client: TestClient,
) -> None:
    tid = _firm(client, "rf7@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "Anand Metals")
        bills = [
            _bill("Anand Metals", "-960.00", date(2026, 8, 1)),
            _bill("Anand Metals", "-40.00", date(2026, 9, 2)),
        ]
        res = apply_outstanding(s, c, balances_from_bills(bills), date(2026, 10, 31))
        s.commit()
        s.refresh(p)
        assert p.opening_balance == D("1000.00") and p.opening_balance_as_of == date(2026, 10, 31)
        assert res.opening_set == 1
        snap = s.scalar(select(PartyTallySnapshot))
        assert snap.outstanding == D("1000.00") and snap.open_bills == 2
        assert snap.oldest_bill_date == date(2026, 8, 1)


def test_a_credit_balance_is_negative_and_a_party_with_no_bills_is_zero(client: TestClient) -> None:
    tid = _firm(client, "rf8@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        adv = _party(s, tid, "Advance Co")
        quiet = _party(s, tid, "Quiet Co")
        record_master_guid(s, tid, c, "party", quiet, "g-q")  # linked here, owes nothing
        apply_outstanding(s, c, balances_from_bills([_bill("Advance Co", "500.00")]), date(2026, 10, 31))
        s.refresh(adv)
        assert adv.opening_balance == D("-500.00")
        snaps = {x.party_id: x for x in s.scalars(select(PartyTallySnapshot))}
        assert snaps[quiet.id].outstanding == D("0.00") and snaps[quiet.id].open_bills == 0


def test_a_hand_typed_balance_is_kept_and_the_difference_is_reported(client: TestClient) -> None:
    tid = _firm(client, "rf9@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "Anand Metals")
        p.opening_balance = D("300.00")
        res = apply_outstanding(s, c, balances_from_bills([_bill("Anand Metals", "-1000.00")]), date(2026, 10, 31))
        assert p.opening_balance == D("300.00") and res.opening_set == 0
        assert res.differences == [
            {"party_id": p.id, "party": "Anand Metals", "ours": "300.00", "tally": "1000.00"}
        ]


def test_a_party_with_history_gets_the_snapshot_but_its_opening_balance_stays(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _firm(client, "rf10@x.example.com")
    monkeypatch.setattr(refill, "has_ledger_history", lambda session, party_id: True)
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "Anand Metals")
        res = apply_outstanding(s, c, balances_from_bills([_bill("Anand Metals", "-1000.00")]), date(2026, 10, 31))
        assert (p.opening_balance or 0) == 0 and res.kept_history == 1
        assert s.scalar(select(PartyTallySnapshot.outstanding)) == D("1000.00")


def test_an_unknown_party_in_the_report_is_listed_not_created(client: TestClient) -> None:
    tid = _firm(client, "rf11@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        res = apply_outstanding(s, c, balances_from_bills([_bill("Nobody Ltd", "-5.00")]), date(2026, 10, 31))
        assert res.unmatched == ["Nobody Ltd"] and res.matched == 0


# -------------------------------------------------------------- last purchase and last rate


def _sales_xml(rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    """rows: (guid, day, number, party, item, rate)"""
    out = "<ENVELOPE>"
    for i, (guid, day, number, party, item, rate) in enumerate(rows, 1):
        out += (
            f"<VOUCHER><GUID>{guid}</GUID><MASTERID>{i}</MASTERID>"
            f"<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><DATE>{day}</DATE>"
            f"<VOUCHERNUMBER>{number}</VOUCHERNUMBER><PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>"
            f"<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>{item}</STOCKITEMNAME>"
            f"<RATE>{rate}/Nos</RATE><BILLEDQTY> 10 Nos</BILLEDQTY><AMOUNT>{D(rate) * 10}</AMOUNT>"
            f"</ALLINVENTORYENTRIES.LIST>"
            f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{party}</LEDGERNAME><ISPARTYLEDGER>Yes</ISPARTYLEDGER>"
            f"<AMOUNT>-{D(rate) * 10}</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>"
        )
    return (out + "</ENVELOPE>").encode()


def test_the_latest_rate_per_party_and_item_wins_whatever_the_file_order(
    client: TestClient,
) -> None:
    tid = _firm(client, "rf12@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p, it = _party(s, tid, "Anand Metals"), _item(s, tid, "SS Thali")
        vs = parse_vouchers(
            _sales_xml(
                [
                    ("g2", "20260905", "9", "Anand Metals", "SS Thali", "110.00"),
                    ("g1", "20260901", "3", "Anand Metals", "SS Thali", "96.00"),
                ]
            )
        )
        res = apply_sales(s, c, vs)
        s.commit()
        row = s.scalar(select(PartyItemPrice))
        assert (row.party_id, row.item_id) == (p.id, it.id)
        assert row.rate == D("110.00") and row.sold_on == date(2026, 9, 5)
        assert row.voucher_no == "9" and row.unit == "Nos" and row.quantity == D("10")
        assert (res.vouchers, res.prices, res.parties) == (2, 1, 1)
        snap = s.scalar(select(PartyTallySnapshot))
        assert snap.last_sale_date == date(2026, 9, 5) and snap.last_sale_amount == D("1100.00")
        assert snap.sales_in_window == 2


def test_an_older_window_does_not_overwrite_a_later_price(client: TestClient) -> None:
    tid = _firm(client, "rf13@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _party(s, tid, "Anand Metals")
        _item(s, tid, "SS Thali")
        apply_sales(
            s, c, parse_vouchers(_sales_xml([("g2", "20260905", "9", "Anand Metals", "SS Thali", "110.00")]))
        )
        apply_sales(
            s, c, parse_vouchers(_sales_xml([("g1", "20260101", "1", "Anand Metals", "SS Thali", "80.00")]))
        )
        assert s.scalar(select(PartyItemPrice.rate)) == D("110.00")
        assert s.scalar(select(PartyTallySnapshot.last_sale_date)) == date(2026, 9, 5)


def test_each_party_keeps_its_own_price_for_the_same_item(client: TestClient) -> None:
    tid = _firm(client, "rf14@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _party(s, tid, "Anand")
        _party(s, tid, "Balaji")
        _item(s, tid, "SS Thali")
        apply_sales(
            s,
            c,
            parse_vouchers(
                _sales_xml(
                    [
                        ("a", "20260901", "1", "Anand", "SS Thali", "100.00"),
                        ("b", "20260902", "2", "Balaji", "SS Thali", "90.00"),
                    ]
                )
            ),
        )
        assert sorted(s.scalars(select(PartyItemPrice.rate)).all()) == [D("90.00"), D("100.00")]


def test_cancelled_optional_and_non_sales_vouchers_leave_no_price(client: TestClient) -> None:
    tid = _firm(client, "rf15@x.example.com")
    raw = _sales_xml([("c", "20260901", "1", "Anand", "SS Thali", "100.00")]).replace(
        b"<VOUCHERNUMBER>", b"<ISCANCELLED>Yes</ISCANCELLED><VOUCHERNUMBER>"
    )
    purchase = _sales_xml([("p", "20260902", "2", "Anand", "SS Thali", "50.00")]).replace(
        b"Sales</VOUCHERTYPENAME>", b"Purchase</VOUCHERTYPENAME>"
    )
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _party(s, tid, "Anand")
        _item(s, tid, "SS Thali")
        res = apply_sales(s, c, parse_vouchers(raw) + parse_vouchers(purchase))
        assert res.vouchers == 0 and s.scalar(select(PartyItemPrice)) is None


def test_unmatched_parties_and_items_are_listed_and_priced_nowhere(client: TestClient) -> None:
    tid = _firm(client, "rf16@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _party(s, tid, "Anand")
        res = apply_sales(
            s,
            c,
            parse_vouchers(
                _sales_xml(
                    [
                        ("a", "20260901", "1", "Anand", "Unknown Item", "100.00"),
                        ("b", "20260902", "2", "Stranger", "Unknown Item", "90.00"),
                    ]
                )
            ),
        )
        assert res.unmatched_items == ["Unknown Item"] and res.unmatched_parties == ["Stranger"]
        assert s.scalar(select(PartyItemPrice)) is None


def test_documents_with_items_that_are_not_sales_are_reported_not_silently_dropped(client: TestClient) -> None:
    tid = _firm(client, "rf20@x.example.com")
    xml = _sales_xml(
        [
            ("a", "20260901", "1", "Anand", "SS Thali", "100.00"),
            ("b", "20260902", "2", "Anand", "SS Thali", "90.00"),
            ("c", "20260903", "3", "Anand", "SS Thali", "80.00"),
        ]
    )
    # the second is the shop's own "Estimate" under Sales; the third an order, which is not a sale
    xml = xml.replace(b"<VOUCHERNUMBER>2<", b"<VOUCHERNUMBER>2<").replace(
        b"<GUID>b</GUID><MASTERID>2</MASTERID><VOUCHERTYPENAME>Sales<", b"<GUID>b</GUID><MASTERID>2</MASTERID><VOUCHERTYPENAME>Estimate<"
    ).replace(
        b"<GUID>c</GUID><MASTERID>3</MASTERID><VOUCHERTYPENAME>Sales<", b"<GUID>c</GUID><MASTERID>3</MASTERID><VOUCHERTYPENAME>Sales Order<"
    )
    from app.services.tally.voucher_xml import parse_vouchers

    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        _party(s, tid, "Anand")
        _item(s, tid, "SS Thali")
        types = {"Sales": "Sales", "Estimate": "Sales", "Sales Order": "Sales Order"}
        res = apply_sales(s, c, parse_vouchers(xml, types))
        assert res.vouchers == 2  # the sale and the estimate count
        assert res.not_counted == {"Sales Order": 1}  # and the owner is told what was left out
        assert s.scalar(select(PartyItemPrice.rate)) == D("90.00")  # the later of the two that count


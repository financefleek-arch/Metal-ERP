"""Last rates from a voucher file the shop exports from Tally (iter_vouchers + the upload route)."""

# ruff: noqa: F811
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import PartyItemPrice, PartyTallySnapshot, TallySyncJob
from app.services.tally.companies import add_company
from app.services.tally.voucher_xml import iter_vouchers, parse_vouchers
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_owner_companies import _agent, _colleague, _h
from tests.test_tally_links import _item, _party

FIX = Path(__file__).parent / "fixtures"
D = Decimal
BASE = "/api/tally/companies"


def _company(tid: str) -> str:
    shop_id = _agent(tid)
    with SessionLocal() as s:
        c = add_company(s, tid, "GST", shop_id=shop_id)
        s.commit()
        return c.id


def _ui_export(rows: list[tuple[str, str, str, str, str]], vtype: str = "Sales") -> str:
    """What Tally's own Export (XML, Data Interchange) writes: TALLYMESSAGE wrappers, INVENTORYENTRIES, no GUID."""
    out = "<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>"
    for day, number, party, item, rate in rows:
        total = D(rate) * 10
        out += (
            f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{vtype}" ACTION="Create">'
            f"<DATE>{day}</DATE><VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME><VOUCHERNUMBER>{number}</VOUCHERNUMBER>"
            f"<PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>"
            f"<INVENTORYENTRIES.LIST><STOCKITEMNAME>{item}</STOCKITEMNAME><RATE>{rate}/Nos</RATE>"
            f"<BILLEDQTY> 10 Nos</BILLEDQTY><AMOUNT>{total}</AMOUNT>"
            f"<INDENTNO>&#4; Not Applicable</INDENTNO></INVENTORYENTRIES.LIST>"
            f"<LEDGERENTRIES.LIST><LEDGERNAME>{party}</LEDGERNAME><ISPARTYLEDGER>Yes</ISPARTYLEDGER>"
            f"<AMOUNT>-{total}</AMOUNT></LEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE>"
        )
    return out + "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"


# ----------------------------------------------------------------------------- the reader


def test_the_stream_reader_gives_what_the_whole_file_parser_gives() -> None:
    raw = (FIX / "tally_vouchers_dev_sample.xml").read_bytes()
    whole = parse_vouchers(raw)
    streamed = list(iter_vouchers(io.BytesIO(raw)))
    assert [(v.guid, v.kind, v.day, v.party, v.party_amount) for v in streamed] == [
        (v.guid, v.kind, v.day, v.party, v.party_amount) for v in whole
    ]
    assert sum(len(v.items) for v in streamed) == sum(len(v.items) for v in whole) > 0


def test_a_ui_export_with_no_guid_is_keyed_by_type_number_and_date() -> None:
    xml = _ui_export([("20261005", "9", "Anand", "SS Thali", "110.00")])
    (v,) = list(iter_vouchers(io.BytesIO(xml.encode("utf-8"))))
    assert v.guid == "Sales|9|20261005" and v.kind == "sales" and v.party == "Anand"
    assert v.items[0].rate == D("110.00") and v.items[0].unit == "Nos" and v.party_amount == D("1100.00")


def test_utf16_exports_are_read_the_same() -> None:
    xml = _ui_export([("20261005", "9", "Anand", "SS Thali", "110.00")])
    (v,) = list(iter_vouchers(io.BytesIO(xml.encode("utf-16"))))  # with the byte-order mark Tally writes
    assert v.number == "9" and v.items[0].item == "SS Thali"


def test_a_control_character_split_across_two_reads_is_still_dropped() -> None:
    base = _ui_export([("20261005", "9", "Anand", "SS Thali", "110.00")])
    for pad in range(0, 40):  # slide the "&#4;" over every position of a 16-byte hold-back window
        xml = base.replace("<ENVELOPE>", "<ENVELOPE>" + " " * pad, 1)
        (v,) = list(iter_vouchers(io.BytesIO(xml.encode("utf-8"))))
        assert v.number == "9", pad


def test_a_file_with_no_vouchers_reads_as_nothing() -> None:
    assert list(iter_vouchers(io.BytesIO(b"<ENVELOPE><BODY/></ENVELOPE>"))) == []
    assert list(iter_vouchers(io.BytesIO(b""))) == []


# ---------------------------------------------------------------------------------- the route


def _post(client: TestClient, tok: str, cid: str, xml: str | bytes, name: str = "daybook.xml"):
    body = xml if isinstance(xml, bytes) else xml.encode("utf-8")
    return client.post(
        f"{BASE}/{cid}/refill/sales-file", headers=_h(tok), files={"file": (name, body, "application/xml")}
    )


def test_a_file_sets_last_rates_and_last_sales(client: TestClient) -> None:
    tok, tid = _register_firm(client, "sf1@x.example.com")
    cid = _company(tid)
    with SessionLocal() as s:
        p, it = _party(s, tid, "Anand Metals"), _item(s, tid, "SS Thali")
        s.commit()
        pid, iid = p.id, it.id
    xml = _ui_export(
        [
            ("20261001", "7", "Anand Metals", "SS Thali", "96.00"),
            ("20261005", "9", "Anand Metals", "SS Thali", "110.00"),
            ("20261006", "10", "Stranger", "SS Thali", "50.00"),
            ("20261007", "11", "Anand Metals", "Unknown Item", "20.00"),
        ]
    )
    r = _post(client, tok, cid, xml)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["vouchers_read"], out["sales"], out["prices"], out["parties"]) == (4, 4, 1, 1)
    assert out["unmatched_parties"] == ["Stranger"] and out["unmatched_items"] == ["Unknown Item"]
    assert out["hint"] is None
    with SessionLocal() as s:
        row = s.scalar(select(PartyItemPrice))
        assert (row.party_id, row.item_id, row.rate, row.sold_on) == (pid, iid, D("110.00"), date(2026, 10, 5))
        assert row.voucher_no == "9" and row.unit == "Nos"
        snap = s.scalar(select(PartyTallySnapshot))
        assert snap.last_sale_date == date(2026, 10, 7)
        job = s.scalar(select(TallySyncJob).where(TallySyncJob.kind == "sales_file"))
        assert job.counts["prices"] == 1 and job.counts["file"] == "daybook.xml"


def test_a_later_file_never_lowers_a_newer_price(client: TestClient) -> None:
    tok, tid = _register_firm(client, "sf2@x.example.com")
    cid = _company(tid)
    with SessionLocal() as s:
        _party(s, tid, "Anand Metals")
        _item(s, tid, "SS Thali")
        s.commit()
    _post(client, tok, cid, _ui_export([("20261005", "9", "Anand Metals", "SS Thali", "110.00")]))
    _post(client, tok, cid, _ui_export([("20260801", "3", "Anand Metals", "SS Thali", "80.00")]))
    with SessionLocal() as s:
        assert s.scalar(select(PartyItemPrice.rate)) == D("110.00")


def test_a_shops_own_types_follow_the_parents_the_last_refresh_read(client: TestClient) -> None:
    tok, tid = _register_firm(client, "sf3@x.example.com")
    cid = _company(tid)
    with SessionLocal() as s:
        _party(s, tid, "Anand Metals")
        _item(s, tid, "SS Thali")
        s.add(
            TallySyncJob(
                tenant_id=tid,
                company_id=cid,
                direction="in",
                kind="pull_vouchers",
                status="ok",
                counts={"voucher_types": {"Estimate": "Sales", "Sales Order": "Sales Order"}},
            )
        )
        s.commit()
    xml = _ui_export([("20261005", "1", "Anand Metals", "SS Thali", "90.00")], vtype="Estimate")
    xml2 = _ui_export([("20261006", "2", "Anand Metals", "SS Thali", "70.00")], vtype="Sales Order")
    both = xml.replace("</REQUESTDATA>", "") .replace("</IMPORTDATA></BODY></ENVELOPE>", "") + xml2.split("<REQUESTDATA>")[1]
    out = _post(client, tok, cid, both).json()
    assert out["sales"] == 1 and out["not_counted"] == {"Sales Order": 1}  # the estimate counts, the order is reported
    with SessionLocal() as s:
        assert s.scalar(select(PartyItemPrice.rate)) == D("90.00")


def test_hints_say_what_to_do_when_nothing_was_set(client: TestClient) -> None:
    tok, tid = _register_firm(client, "sf4@x.example.com")
    cid = _company(tid)
    with SessionLocal() as s:
        _party(s, tid, "Anand Metals")
        s.commit()
    assert "No vouchers were found" in _post(client, tok, cid, b"<ENVELOPE/>").json()["hint"]
    purchase = _ui_export([("20261005", "9", "Anand Metals", "SS Thali", "110.00")], vtype="Purchase")
    assert "none are sales" in _post(client, tok, cid, purchase).json()["hint"]
    nomatch = _ui_export([("20261005", "9", "Anand Metals", "SS Thali", "110.00")])
    assert "Pull items from Tally first" in _post(client, tok, cid, nomatch).json()["hint"]


def test_only_someone_who_can_write_may_load_a_file_and_another_firm_cannot(client: TestClient) -> None:
    tok, tid = _register_firm(client, "sf5@x.example.com")
    cid = _company(tid)
    viewer = _colleague(client, tid, "viewer")
    xml = _ui_export([("20261005", "9", "Anand", "SS Thali", "110.00")]).encode()
    r = client.post(f"{BASE}/{cid}/refill/sales-file", headers=viewer, files={"file": ("a.xml", xml, "application/xml")})
    assert r.status_code == 403
    other_tok, _ = _register_firm(client, "sf5b@x.example.com")
    assert _post(client, other_tok, cid, xml).status_code == 404


def test_a_file_over_the_limit_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import tally_companies

    monkeypatch.setattr(tally_companies, "SALES_FILE_MAX_BYTES", 100)
    tok, tid = _register_firm(client, "sf6@x.example.com")
    cid = _company(tid)
    r = _post(client, tok, cid, b"<ENVELOPE>" + b" " * 500 + b"</ENVELOPE>")
    assert r.status_code == 413

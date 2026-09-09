"""F1b-1 — sales-voucher-out.

`build_sales_voucher_xml_bytes` is unit-tested here against plain (not
DB-persisted) `Invoice`/`InvoiceLine`/`Party`/`Item` objects — the
serializer only reads attributes, no session needed. The golden-XML shape
matches what was live-verified against a real TallyPrime gateway
2026-09-09 (see docs/EXECUTION-PLAN-F1b1-sales-voucher-push.md, "Live
probe findings" + "Finding 3"): round-off folded into the last line's
amount (no separate ledger entry), UTF-8 encoding (not the purchase
side's UTF-16 file default), sign convention (party debit negative,
Sales credit positive, bill-wise "New Ref").

`push_readiness` / `enqueue_push_sales` / `process_push_result` are
integration-level and use the DB session fixture.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from app.models import Invoice, InvoiceLine, Item, Party
from app.models._mixins import InvoiceStatus, PartyRole
from app.services.invoices.tally_xml import (
    LedgerMapIncomplete,
    build_sales_voucher_envelope,
    build_sales_voucher_xml_bytes,
)

_NS = {"UDF": "urn:metalerp:tally-udf"}


def _party(name: str = "Anand Metals") -> Party:
    return Party(id="party-1", tenant_id="t1", legal_name=name, role=PartyRole.customer)


def _item(name: str) -> Item:
    return Item(id=f"item-{name}", tenant_id="t1", name=name, name_normalized=name.lower())


def _line(
    sl_no: int,
    item: Item,
    *,
    qty: str,
    rate: str,
    line_total: str,
    uom: str = "Nos",
    description: str | None = None,
) -> InvoiceLine:
    ln = InvoiceLine(
        id=f"line-{sl_no}",
        invoice_id="inv-1",
        sl_no=sl_no,
        item_id=item.id,
        description=description or item.name,
        quantity=Decimal(qty),
        uom=uom,
        unit_rate=Decimal(rate),
        discount=Decimal("0"),
        line_total=Decimal(line_total),
    )
    ln.item = item  # type: ignore[attr-defined]  # viewonly relationship, set directly for the test
    return ln


def _invoice(
    lines: list[InvoiceLine],
    *,
    number: int = 42,
    grand_total: str,
    round_off: str,
    weighment_slips: list | None = None,
) -> Invoice:
    inv = Invoice(
        id="inv-1",
        tenant_id="t1",
        number=number,
        fy="2026-27",
        date=date(2026, 9, 2),
        status=InvoiceStatus.final,
        subtotal=sum((ln.line_total for ln in lines), Decimal("0")),
        discount_total=Decimal("0"),
        round_off=Decimal(round_off),
        grand_total=Decimal(grand_total),
        weighment_slips=weighment_slips,
    )
    inv.lines = lines  # type: ignore[assignment]
    return inv


_LEDGER_MAP = {"sales_ledger": "Sales Accounts"}


# --------------------------------------------------------------------------
# golden XML — the exact shape live-verified against TallyPrime
# --------------------------------------------------------------------------


def test_single_line_voucher_matches_live_verified_shape():
    item = _item("SS Thali 11in")
    line = _line(1, item, qty="10", rate="96.00", line_total="960.00")
    inv = _invoice([line], number=42, grand_total="960.20", round_off="0.20")
    party = _party("Anand Metals")

    env = build_sales_voucher_envelope(inv, party, _LEDGER_MAP)

    assert env.find(".//TALLYREQUEST").text == "Import Data"
    assert env.find(".//REPORTNAME").text == "Vouchers"

    vch = env.find(".//VOUCHER")
    assert vch.get("VCHTYPE") == "Sales"
    assert vch.get("ACTION") == "Create"
    assert vch.find("DATE").text == "20260902"
    assert vch.find("EFFECTIVEDATE").text == "20260902"
    assert vch.find("VOUCHERTYPENAME").text == "Sales"
    assert vch.find("VOUCHERNUMBER").text == "42"
    assert vch.find("PARTYLEDGERNAME").text == "Anand Metals"
    assert vch.find("PERSISTEDVIEW").text == "Invoice Voucher View"

    udf = vch.find("UDF:METALERP_REF", namespaces=_NS)
    assert udf is not None
    assert udf.text == "inv_inv-1"

    # exactly one inventory entry, round-off folded into its amount
    inv_entries = vch.findall("ALLINVENTORYENTRIES.LIST")
    assert len(inv_entries) == 1
    ie = inv_entries[0]
    assert ie.find("STOCKITEMNAME").text == "SS Thali 11in"
    assert ie.find("ISDEEMEDPOSITIVE").text == "No"
    assert ie.find("RATE").text == "96.00/Nos"
    assert ie.find("AMOUNT").text == "960.20"  # 960.00 + 0.20 round_off
    assert ie.find("ACTUALQTY").text == "10 Nos"
    assert ie.find("BILLEDQTY").text == "10 Nos"

    alloc = ie.find("ACCOUNTINGALLOCATIONS.LIST")
    assert alloc.find("LEDGERNAME").text == "Sales Accounts"
    assert alloc.find("ISDEEMEDPOSITIVE").text == "No"
    assert alloc.find("AMOUNT").text == "960.20"

    # NO standalone Round Off LEDGERENTRIES.LIST — exactly one, the party's
    ledger_entries = vch.findall("LEDGERENTRIES.LIST")
    assert len(ledger_entries) == 1
    le = ledger_entries[0]
    assert le.find("LEDGERNAME").text == "Anand Metals"
    assert le.find("ISDEEMEDPOSITIVE").text == "Yes"
    assert le.find("AMOUNT").text == "-960.20"

    bw = le.find("BILLALLOCATIONS.LIST")
    assert bw.find("NAME").text == "42"
    assert bw.find("BILLTYPE").text == "New Ref"
    assert bw.find("AMOUNT").text == "-960.20"

    # no NARRATION when there are no weighment slips
    assert vch.find("NARRATION") is None


def test_multi_line_voucher_only_last_line_carries_round_off():
    it1, it2 = _item("SS Patta"), _item("SS Thali")
    l1 = _line(1, it1, qty="5", rate="200.00", line_total="1000.00")
    l2 = _line(2, it2, qty="2", rate="80.00", line_total="160.00")
    inv = _invoice([l1, l2], grand_total="1160.30", round_off="0.30")
    party = _party()

    env = build_sales_voucher_envelope(inv, party, _LEDGER_MAP)
    vch = env.find(".//VOUCHER")
    entries = vch.findall("ALLINVENTORYENTRIES.LIST")
    assert len(entries) == 2
    assert entries[0].find("AMOUNT").text == "1000.00"  # unchanged
    assert entries[1].find("AMOUNT").text == "160.30"  # +0.30 round_off
    # voucher still nets to zero: 1000.00 + 160.30 == 1160.30 == grand_total
    le = vch.find("LEDGERENTRIES.LIST")
    assert le.find("AMOUNT").text == "-1160.30"


def test_blank_description_line_excluded():
    item = _item("Real Item")
    real = _line(1, item, qty="1", rate="10.00", line_total="10.00")
    blank = InvoiceLine(
        id="line-blank", invoice_id="inv-1", sl_no=2, description="",
        quantity=Decimal("0"), unit_rate=Decimal("0"), discount=Decimal("0"),
        line_total=Decimal("0"),
    )
    inv = _invoice([real, blank], grand_total="10.00", round_off="0")
    party = _party()

    env = build_sales_voucher_envelope(inv, party, _LEDGER_MAP)
    entries = env.find(".//VOUCHER").findall("ALLINVENTORYENTRIES.LIST")
    assert len(entries) == 1
    assert entries[0].find("STOCKITEMNAME").text == "Real Item"


def test_weighment_narration_rendered_when_slips_present():
    item = _item("Scrap")
    line = _line(1, item, qty="487.5", rate="50.00", line_total="24375.00", uom="kg")
    inv = _invoice(
        [line], grand_total="24375.00", round_off="0",
        weighment_slips=[{"seg": 1, "recorded_kg": "487.50"}],
    )
    party = _party()

    env = build_sales_voucher_envelope(inv, party, _LEDGER_MAP)
    narration = env.find(".//VOUCHER/NARRATION")
    assert narration is not None
    assert "seg 1" in narration.text
    assert "487.50kg" in narration.text


def test_no_weighment_slips_no_narration():
    item = _item("X")
    line = _line(1, item, qty="1", rate="1", line_total="1")
    inv = _invoice([line], grand_total="1", round_off="0", weighment_slips=[])
    party = _party()

    env = build_sales_voucher_envelope(inv, party, _LEDGER_MAP)
    assert env.find(".//VOUCHER/NARRATION") is None


# --------------------------------------------------------------------------
# error paths
# --------------------------------------------------------------------------


def test_missing_sales_ledger_raises_ledger_map_incomplete():
    item = _item("X")
    line = _line(1, item, qty="1", rate="1", line_total="1")
    inv = _invoice([line], grand_total="1", round_off="0")
    party = _party()

    with pytest.raises(LedgerMapIncomplete):
        build_sales_voucher_envelope(inv, party, {})

    with pytest.raises(LedgerMapIncomplete):
        build_sales_voucher_envelope(inv, party, {"sales_ledger": ""})


def test_round_off_ledger_not_required():
    """round_off_ledger is a valid, harmless key to have around (F1a
    collects it) but this serializer never reads it — confirms the ledger
    map only needs sales_ledger."""
    item = _item("X")
    line = _line(1, item, qty="1", rate="1", line_total="1")
    inv = _invoice([line], grand_total="1", round_off="0")
    party = _party()

    env = build_sales_voucher_envelope(
        inv, party, {"sales_ledger": "Sales Accounts"}  # no round_off_ledger key at all
    )
    assert env is not None


def test_no_real_lines_raises_value_error():
    blank = InvoiceLine(
        id="line-blank", invoice_id="inv-1", sl_no=1, description="  ",
        quantity=Decimal("0"), unit_rate=Decimal("0"), discount=Decimal("0"),
        line_total=Decimal("0"),
    )
    inv = _invoice([blank], grand_total="0", round_off="0")
    party = _party()

    with pytest.raises(ValueError, match="no real lines"):
        build_sales_voucher_envelope(inv, party, _LEDGER_MAP)


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


def test_build_bytes_defaults_to_utf8_not_utf16():
    """Live-verified 2026-09-09 (Finding 3): a UTF-16 body over the live
    HTTP POST path gets "Unknown Request" from Tally; byte-identical UTF-8
    content is accepted. This default must never silently flip back to
    UTF-16 (the purchase side's file-based default)."""
    item = _item("X")
    line = _line(1, item, qty="1", rate="1", line_total="1")
    inv = _invoice([line], grand_total="1", round_off="0")
    party = _party()

    xml = build_sales_voucher_xml_bytes(inv, party, _LEDGER_MAP)
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")

    parsed = etree.fromstring(xml)
    assert parsed.find(".//VOUCHER").get("VCHTYPE") == "Sales"


def test_build_bytes_honours_explicit_encoding_override():
    item = _item("X")
    line = _line(1, item, qty="1", rate="1", line_total="1")
    inv = _invoice([line], grand_total="1", round_off="0")
    party = _party()

    xml = build_sales_voucher_xml_bytes(inv, party, _LEDGER_MAP, encoding="UTF-16")
    assert xml.startswith(b"\xff\xfe")  # UTF-16LE BOM
    assert b"U\x00T\x00F\x00-\x001\x006\x00" in xml[:80]  # "UTF-16" as UTF-16LE code units

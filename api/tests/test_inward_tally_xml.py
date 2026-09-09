"""F5d — `inward/tally_xml.py`'s live-push encoding behavior.

The purchase-voucher shape itself (inventory entries, GST split, bill-wise
allocation, UDF ref) already has a golden-XML assertion in
`tests/inward/test_api_flow.py::test_full_approve_creates_masters_and_xml`
(against the file-export path, UTF-16 default). This file only covers what
F5d actually changes: `LedgerConfig.xml_encoding` still defaults to
UTF-16 (the file-export route must keep working byte-for-byte), and an
explicit `xml_encoding="UTF-8"` (what `enqueue_push_purchase` passes for
the live-push path) produces the identical element structure, just a
different `<?xml?>` declaration.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.models import InwardBill, InwardBillLine
from app.services.inward.tally_xml import LedgerConfig, build_xml_bytes


def _bill() -> InwardBill:
    bill = InwardBill(
        tenant_id="t1",
        source_filename="x.pdf",
        supplier_name="ZZTEST Supplier",
        supplier_gstin="19AAAAA0000A1Z5",
        bill_no="BILL-1",
        bill_date=date(2026, 9, 2),
        place_of_supply_state_code="19",
        supply_type="intra",
        taxable_total=Decimal("1000.00"),
        cgst_total=Decimal("90.00"),
        sgst_total=Decimal("90.00"),
        round_off=Decimal("0.30"),
        grand_total=Decimal("1180.30"),
        matched_party_id="party-1",
    )
    bill.lines = [
        InwardBillLine(
            sl_no=1,
            description="Steel Thali",
            hsn="7323",
            quantity=Decimal("10"),
            uom="Nos",
            unit_rate=Decimal("100.00"),
            taxable_value=Decimal("1000.00"),
            cgst_rate=Decimal("9"),
            cgst_amt=Decimal("90.00"),
            sgst_rate=Decimal("9"),
            sgst_amt=Decimal("90.00"),
            line_total=Decimal("1180.00"),
            matched_item_id="item-1",
        )
    ]
    return bill


def test_default_encoding_is_still_utf16_for_file_export() -> None:
    cfg = LedgerConfig()
    assert cfg.xml_encoding == "UTF-16"
    xml_bytes = build_xml_bytes(_bill(), cfg, party_name="ZZTEST Supplier")
    # A genuine UTF-16 body starts with a BOM and is null-byte-interleaved
    # ASCII — decode it back to confirm the declared encoding round-trips,
    # rather than string-searching the raw bytes (which won't contain the
    # literal text "UTF-16" the way a UTF-8 body would).
    assert xml_bytes[:2] in (b"\xff\xfe", b"\xfe\xff")
    text = xml_bytes.decode("utf-16")
    assert "encoding='UTF-16'" in text or 'encoding="UTF-16"' in text
    assert "<ENVELOPE" in text


def test_utf8_override_for_live_push_produces_same_shape() -> None:
    cfg16 = LedgerConfig()
    cfg8 = LedgerConfig(xml_encoding="UTF-8")

    bytes16 = build_xml_bytes(_bill(), cfg16, party_name="ZZTEST Supplier")
    bytes8 = build_xml_bytes(_bill(), cfg8, party_name="ZZTEST Supplier")

    assert b"UTF-8" in bytes8[:60]
    assert bytes16[:2] in (b"\xff\xfe", b"\xfe\xff")  # UTF-16 BOM, not ASCII text

    # Same element structure regardless of the declared encoding — parse
    # both and diff the serialized (canonical) tree, not the raw bytes.
    root16 = etree.fromstring(bytes16)
    root8 = etree.fromstring(bytes8)
    c16 = etree.tostring(root16, method="c14n")
    c8 = etree.tostring(root8, method="c14n")
    assert c16 == c8


def test_purchase_ledger_and_round_off_ledger_from_ledger_map_style_config() -> None:
    """F5d's `enqueue_push_purchase` builds a `LedgerConfig` from
    `tally_company.ledger_map` keys — confirm the resulting envelope uses
    whatever ledger names are passed, same as the existing inward-settings
    path does via `TallyLedgerConfig`.
    """
    cfg = LedgerConfig(
        purchase_ledger="Purchase Accounts (Metal)",
        round_off_ledger="Rounding",
        xml_encoding="UTF-8",
    )
    xml_bytes = build_xml_bytes(_bill(), cfg, party_name="ZZTEST Supplier")
    root = etree.fromstring(xml_bytes)
    ledger_names = {e.text for e in root.findall(".//ACCOUNTINGALLOCATIONS.LIST/LEDGERNAME")}
    assert "Purchase Accounts (Metal)" in ledger_names
    round_off_names = {e.text for e in root.findall(".//LEDGERENTRIES.LIST/LEDGERNAME")}
    assert "Rounding" in round_off_names

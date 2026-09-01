"""Extractor tested against the real Sugal Foods PDF (INV2526-5667).

This is the mandatory real-PDF check: the committed fixture must extract to
the paise. If pdfplumber's reading of this file ever drifts, this fails.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.inward.extract_text import extract
from app.services.inward.reconcile import reconcile
from tests.inward.conftest import SUGAL_PDF


def _ext():
    return extract(str(SUGAL_PDF))


def test_header_fields() -> None:
    e = _ext()
    assert e.supplier_name == "SUGAL FOODS"
    assert e.supplier_gstin == "19BHBPK1450P1Z3"
    assert e.buyer_gstin == "19AALFR8182P1Z3"
    assert e.bill_no == "INV2526-5667"
    assert e.bill_date == "2025-08-25"
    assert e.sales_order_ref == "SO2526-5830"
    assert e.place_of_supply_state_code == "19"
    assert e.page_count == 2
    assert e.low_text is False


def test_supplier_phone_and_address() -> None:
    e = _ext()
    # the header phone ("Phone 8513057060"), NOT the buyer's "+919832137599"
    assert e.supplier_phone == "8513057060"

    a = e.supplier_address
    assert a is not None
    assert a.line1 == "179/1/244 Agrasen Road, Siliguri"
    assert a.city == "Siliguri"
    assert a.state_code == "19"  # West Bengal
    assert a.pincode == "734005"


def test_totals_to_the_paise() -> None:
    e = _ext()
    assert e.taxable_total == Decimal("35970.22")
    assert e.cgst_total == Decimal("3237.32")
    assert e.sgst_total == Decimal("3237.32")
    assert e.igst_total is None
    assert e.round_off == Decimal("0.14")
    assert e.grand_total == Decimal("42445.00")
    assert e.amount_in_words == (
        "Indian Rupee Forty-Two Thousand Four Hundred Forty-Five Only"
    )


def test_reconciles() -> None:
    e = _ext()
    r = reconcile(
        taxable_total=e.taxable_total,
        cgst_total=e.cgst_total,
        sgst_total=e.sgst_total,
        igst_total=e.igst_total,
        round_off=e.round_off,
        grand_total=e.grand_total,
    )
    assert r.reconciled is True
    assert r.discrepancy == Decimal("0.00")


def test_all_twelve_lines() -> None:
    e = _ext()
    assert len(e.lines) == 12
    assert [ln.sl_no for ln in e.lines] == list(range(1, 13))
    # every line: same HSN, Pcs, 3% discount, 9% CGST + 9% SGST
    for ln in e.lines:
        assert ln.hsn == "21069092"
        assert ln.uom == "Pcs"
        assert ln.discount_pct == Decimal("3.00")
        assert ln.cgst_rate == Decimal("9")
        assert ln.sgst_rate == Decimal("9")

    # line 1 — the column-wrap hazard row ("11,689.3\n0", "1,052.04 (\n9%)")
    l1 = e.lines[0]
    assert l1.description == "Monin Mojito Mint Syrup 1000Ml"
    assert l1.quantity == Decimal("18.00")
    assert l1.unit_rate == Decimal("669.49")
    assert l1.cgst_amt == Decimal("1052.04")
    assert l1.sgst_amt == Decimal("1052.04")
    assert l1.line_total == Decimal("11689.30")

    # line 12 — trailing row
    l12 = e.lines[11]
    assert l12.description == "Monin Peach Tea 1000Ml Syrup"
    assert l12.quantity == Decimal("3.00")
    assert l12.line_total == Decimal("1948.22")

    # descriptions never contain a stray newline or double space
    for ln in e.lines:
        assert "\n" not in ln.description
        assert "  " not in ln.description


def test_line_totals_sum_to_taxable() -> None:
    e = _ext()
    s = sum((ln.line_total or Decimal(0) for ln in e.lines), Decimal(0))
    assert s == e.taxable_total

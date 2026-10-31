"""The Stock Summary parser, against a trimmed slice of a real 920-row export."""

from decimal import Decimal
from pathlib import Path

from app.services.tally.stock_summary import is_adjustment, parse_stock_summary

FIX = Path(__file__).parent / "fixtures" / "tally_stock_summary_sample.xml"


def _rows():
    return parse_stock_summary(FIX.read_bytes())


def test_a_normal_row_gives_quantity_unit_rate_and_positive_value():
    row = _rows()[0]
    assert row.name == "1.2 LYR SPARK DOUBLE WALLED KETTLE"
    assert row.qty == Decimal(19) and row.unit == "Pcs"
    assert row.rate == Decimal("772.52")
    assert row.value == Decimal("14677.84")  # Tally writes it -14677.84
    assert not row.adjustment and not row.negative


def test_adjustment_lines_are_flagged_not_dropped():
    flagged = {r.name for r in _rows() if r.adjustment}
    assert {"OTHER", "Rate Difference", "Rate Difference 18%", "SLAB DISCOUNT"} <= flagged
    assert not is_adjustment("1/3 LID")


def test_oversold_stock_keeps_its_negative_quantity():
    neg = [r for r in _rows() if r.negative]
    assert {r.name for r in neg} >= {"ALU PRESSURE COOKER 3 LTR", "MAGIC GAS STOVE"}
    cooker = next(r for r in neg if r.name == "ALU PRESSURE COOKER 3 LTR")
    assert cooker.qty == Decimal(-188) and cooker.unit == "Pcs"


def test_a_row_without_a_quantity_has_none_not_zero():
    rows = [r for r in _rows() if r.name == "OTHER" and r.qty is None]
    assert rows


def test_utf16_with_a_byte_order_mark_is_read_the_same():
    text = FIX.read_text(encoding="utf-8")
    assert len(parse_stock_summary(text.encode("utf-16"))) == len(_rows())


def test_xml_entities_in_names_are_decoded():
    raw = (
        b"<ENVELOPE><DSPACCNAME><DSPDISPNAME>ZIC - SALT &amp; PEPPER</DSPDISPNAME></DSPACCNAME>"
        b"<DSPSTKINFO><DSPSTKCL><DSPCLQTY>300 Pcs</DSPCLQTY><DSPCLRATE>13.00</DSPCLRATE>"
        b"<DSPCLAMTA>-3900.00</DSPCLAMTA></DSPSTKCL></DSPSTKINFO></ENVELOPE>"
    )
    (row,) = parse_stock_summary(raw)
    assert row.name == "ZIC - SALT & PEPPER" and row.value == Decimal("3900.00")

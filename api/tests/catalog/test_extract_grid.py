"""The grid parser: real 2-page fixture + synthetic edge cases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.catalog.extract_grid import NotACatalog, extract
from tests.catalog.conftest import SAMPLE_PDF
from tests.catalog.pdfs import TestCell, make_pdf, photo, text_only_pdf

# --- the real fixture (2 pages cut from the Glassware Stock file) ------------


@pytest.fixture(scope="module")
def sample():
    return extract(SAMPLE_PDF.read_bytes())


def test_fixture_reads_every_cell(sample) -> None:
    assert sample.page_count == 2
    assert len(sample.cells) == 24  # 12 per page
    assert sample.skipped_no_price == 0
    assert sample.warnings == []


def test_fixture_codes_are_unique_and_present(sample) -> None:
    codes = [c.supplier_code for c in sample.cells]
    assert len(set(codes)) == 24
    assert all(codes)


def test_fixture_spot_checks(sample) -> None:
    by_code = {c.supplier_code: c for c in sample.cells}
    first = sample.cells[0]
    assert (first.page_no, first.position) == (1, 1)
    assert first.supplier_code == "AJ-1003"
    assert first.cost_price == Decimal("299")
    assert first.pack_qty == 7
    assert "PUDDING SET" in first.name_raw

    # second page: a 2-pack beer mug
    mug = by_code["Y5813"]
    assert (mug.page_no, mug.position) == (2, 1)
    assert mug.cost_price == Decimal("113")
    assert mug.pack_qty == 2
    assert "BEER MUG" in mug.name_raw

    # a hyphenated code with a suffix
    assert by_code["DZB97-KFT"].cost_price == Decimal("99")


def test_fixture_photos_come_with_bytes_and_size(sample) -> None:
    for c in sample.cells:
        assert c.image[:2] == b"\xff\xd8"  # JPEG
        assert c.image_ext in ("jpeg", "jpg")
        assert c.image_w > 100 and c.image_h > 100


def test_fixture_quotes_in_names_survive(sample) -> None:
    assert any('"' in c.name_raw for c in sample.cells)


# --- synthetic edge cases -----------------------------------------------------


def test_price_without_pack_defaults_to_one() -> None:
    pdf = make_pdf([[TestCell(price_line="Rs 250", code="A1")]])
    c = extract(pdf).cells[0]
    assert c.cost_price == Decimal("250")
    assert c.pack_qty == 1


def test_thousands_separator_and_decimals() -> None:
    pdf = make_pdf([[TestCell(price_line="Rs 1,099.50 for 3 pcs", code="A1")]])
    c = extract(pdf).cells[0]
    assert c.cost_price == Decimal("1099.50")
    assert c.pack_qty == 3


def test_blank_filler_cell_is_skipped_and_counted() -> None:
    cells = [TestCell(code="A1"), TestCell(price_line=None, name_lines=[], code=None)]
    r = extract(make_pdf([cells]))
    assert [c.supplier_code for c in r.cells] == ["A1"]
    assert r.skipped_no_price == 1


def test_wrapped_name_word_on_the_code_line_is_not_the_code() -> None:
    # the real file has "SJ013-380" and a wrapped "CTN" on one text line
    cell = TestCell(
        name_lines=["SJ013-380 DELI SPECIAL 380 ML", "WHISKY GLASS 6 PC COL BOX 8 SET"],
        code="SJ013-380",
        code_line_tail="CTN",
    )
    c = extract(make_pdf([[cell]])).cells[0]
    assert c.supplier_code == "SJ013-380"
    assert c.name_raw.endswith("8 SET CTN")


def test_code_with_a_stray_space_after_a_hyphen_is_normalised() -> None:
    c = extract(make_pdf([[TestCell(code="TXZ-02- L4-HA")]])).cells[0]
    assert c.supplier_code == "TXZ-02-L4-HA"


def test_repeated_code_gets_a_suffix_and_a_warning() -> None:
    r = extract(make_pdf([[TestCell(code="DUP1"), TestCell(code="DUP1")]]))
    assert [c.supplier_code for c in r.cells] == ["DUP1", "DUP1~2"]
    assert any("repeated" in w for w in r.warnings)


def test_missing_code_line_gets_a_placeholder_and_a_warning() -> None:
    cell = TestCell(name_lines=["A name with spaces and no code line"], code=None)
    r = extract(make_pdf([[cell]]))
    assert r.cells[0].supplier_code == "P1-1"
    assert any("no product code" in w for w in r.warnings)


def test_reading_order_is_rows_then_columns() -> None:
    cells = [TestCell(code=f"C{i}") for i in range(6)]
    codes = [c.supplier_code for c in extract(make_pdf([cells])).cells]
    assert codes == [f"C{i}" for i in range(6)]
    assert [c.position for c in extract(make_pdf([cells])).cells] == [1, 2, 3, 4, 5, 6]


def test_small_logos_are_not_products() -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(20, 20, 50, 40), stream=photo())  # 30x20 pt logo
    page.insert_text((20, 60), "Rs 99 for 1 pcs", fontsize=9)
    page.insert_text((20, 72), "LOGO1", fontsize=8)
    data = doc.tobytes()
    doc.close()
    with pytest.raises(NotACatalog):
        extract(data)


def test_a_pdf_without_products_is_rejected_clearly() -> None:
    with pytest.raises(NotACatalog, match="No products found"):
        extract(text_only_pdf())


def test_corrupt_bytes_are_rejected() -> None:
    with pytest.raises(NotACatalog):
        extract(b"%PDF-1.4 this is not really a pdf")

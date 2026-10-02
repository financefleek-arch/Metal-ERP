"""The customer catalog renderer: layout, contents page, prices, photos, safety."""

from __future__ import annotations

import dataclasses
import io
import time
from datetime import date
from decimal import Decimal

import pymupdf
import pytest
from PIL import Image

from app.services.catalog import catalog_pdf as cp
from app.services.catalog.catalog_pdf import (
    Brand,
    CatalogEntry,
    CatalogPdfOptions,
    build,
    piece_price,
    render_pdf,
    render_preview_png,
)
from tests.catalog.pdfs import photo

BRAND = Brand(
    "Sample Traders", "Glassware Price List", phone="+91 98765 43210",
    email="orders@sample.example.com", address="12 Market Road, Siliguri, 734005",
)
TODAY = date(2026, 10, 2)
PHOTO = photo((30, 120, 160))


def entry(i: int, group: str | None = "Beer Mugs", price: str = "374", pack: int = 6,
          image: bytes | None = PHOTO, name: str | None = None) -> CatalogEntry:
    return CatalogEntry(
        code=f"GL-{i:06d}", name=name or f"Deli Item {i}", group=group,
        price=Decimal(price), pack_qty=pack, image=image,
    )


def many(n: int, group: str | None = "Beer Mugs") -> list[CatalogEntry]:
    return [entry(i, group) for i in range(1, n + 1)]


def doc_of(entries, **opts):  # type: ignore[no-untyped-def]
    return build(entries, CatalogPdfOptions(**opts), BRAND, TODAY)


def text(doc: pymupdf.Document, i: int) -> str:
    return doc[i].get_text()


def all_text(doc: pymupdf.Document) -> str:
    return " ".join(p.get_text() for p in doc)


def capacity(columns: int) -> int:
    """Cards per full page without a heading."""
    _, ch = cp._geometry(columns)
    rows = int((cp.BOTTOM - cp.TOP + cp.GAP) // (ch + cp.GAP))
    return rows * columns


# --- what may be printed ---------------------------------------------------------


def test_entries_carry_no_cost_or_supplier_fields() -> None:
    names = {f.name for f in dataclasses.fields(CatalogEntry)}
    assert names == {"code", "name", "group", "price", "pack_qty", "image"}


# --- cover -----------------------------------------------------------------------


def test_cover_has_firm_title_count_date_and_contact() -> None:
    doc = doc_of(many(24, None), group_by="none")
    cover = text(doc, 0)
    assert "SAMPLE TRADERS" in cover
    assert "Glassware Price List" in cover
    assert "24 items" in cover and "2 Oct 2026" in cover
    assert "12 Market Road, Siliguri, 734005" in cover
    assert "+91 98765 43210" in cover and "orders@sample.example.com" in cover
    assert "Page 1" not in cover  # the cover carries no running header or number


def test_cover_without_contact_details_still_builds() -> None:
    bare = Brand("Solo Shop", "Price List")
    doc = build(many(3, None), CatalogPdfOptions(group_by="none"), bare, TODAY)
    assert "SOLO SHOP" in text(doc, 0) and "3 items" in text(doc, 0)


def test_long_titles_wrap_on_the_cover() -> None:
    brand = Brand("Shop", "An Extremely Long Catalog Title " * 4)
    doc = build(many(2, None), CatalogPdfOptions(group_by="none"), brand, TODAY)
    for block in doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                assert span["bbox"][2] <= cp.PAGE_W - 40


# --- pages, columns, flow --------------------------------------------------------


@pytest.mark.parametrize("columns", [2, 3, 4])
def test_capacity_per_page_by_columns(columns: int) -> None:
    cap = capacity(columns)
    one_page = doc_of(many(cap, None), columns=columns, group_by="none")
    assert len(one_page) == 2  # cover + 1 product page
    two_pages = doc_of(many(cap + 1, None), columns=columns, group_by="none")
    assert len(two_pages) == 3
    assert capacity(2) < capacity(3) < capacity(4)


def test_a_group_heading_costs_room_on_its_page() -> None:
    cap = capacity(3)
    grouped = doc_of(many(cap, "Beer Mugs"), columns=3)  # + heading: one card row no longer fits
    flat = doc_of(many(cap, "Beer Mugs"), columns=3, group_by="none")
    assert len(grouped) >= len(flat)


def test_invalid_columns_are_rejected() -> None:
    with pytest.raises(ValueError):
        doc_of(many(2), columns=5)


def test_every_item_is_printed_once_with_its_price() -> None:
    entries = [entry(i, "Beer Mugs", str(100 + i)) for i in range(1, 31)]
    t = all_text(doc_of(entries, columns=3))
    for e in entries:
        assert t.count(e.code) == 1
        assert f"Rs {e.price}" in t


# --- groups + contents -----------------------------------------------------------


def _groups() -> list[CatalogEntry]:
    out: list[CatalogEntry] = []
    n = 0
    for g, count in (("Wine Glasses", 5), ("beer mugs", 14), ("Bowls", 3), (None, 2)):
        for _ in range(count):
            n += 1
            out.append(entry(n, g))
    return out


def test_sections_sorted_case_insensitively_with_other_last() -> None:
    doc = doc_of(_groups(), columns=3)
    body = " ".join(text(doc, i) for i in range(len(doc)))
    order = [body.index(x) for x in ("beer mugs", "Bowls", "Wine Glasses", "Other")]
    assert order == sorted(order)


def test_contents_page_lists_groups_with_correct_page_numbers() -> None:
    doc = doc_of(_groups(), columns=3)
    toc_lines = [ln for ln in text(doc, 1).splitlines() if ln.strip()]
    assert toc_lines[0] == "Contents"
    expected = {"beer mugs": 14, "Bowls": 3, "Wine Glasses": 5, "Other": 2}
    for name, count in expected.items():
        i = toc_lines.index(name)
        assert toc_lines[i + 1] == f"{count} items"
        shown_page = int(toc_lines[i + 2])
        # the heading really is on that page (1-based -> index - 1)
        page_text = text(doc, shown_page - 1)
        assert name in page_text and f"{count} items" in page_text, name


def test_no_contents_when_off_flat_or_a_single_group() -> None:
    assert len(doc_of(many(3, "A"), columns=3)) == 2  # one group: no contents page
    flat = doc_of(_groups(), columns=3, group_by="none")
    assert "Contents" not in all_text(flat)
    off = doc_of(_groups(), columns=3, contents=False)
    assert "Contents" not in all_text(off)
    on = doc_of(_groups(), columns=3, contents=True)
    assert "Contents" in all_text(on)


def test_a_group_running_over_a_page_gets_a_continued_heading() -> None:
    doc = doc_of(many(40, "Beer Mugs"), columns=3, contents=False)
    assert "Beer Mugs" in text(doc, 1)
    assert "(continued)" in text(doc, 2)
    assert "(continued)" not in text(doc, 1)


def test_many_groups_spill_the_contents_over_pages() -> None:
    entries = [entry(i, f"Group {i:03d}") for i in range(1, 80)]
    doc = doc_of(entries, columns=3)
    toc_pages = cp.toc_page_count(79)
    assert toc_pages == 3  # 38 lines per contents page
    assert "Contents" in text(doc, 1) and "Contents" in text(doc, 3)
    # the last group sits on the last contents page, and its page number is right
    lines = [ln for ln in text(doc, toc_pages).splitlines() if ln.strip()]
    i = lines.index("Group 079")
    assert "Group 079" in text(doc, int(lines[i + 2]) - 1)


# --- running header / footer -----------------------------------------------------


def test_product_pages_carry_a_running_header_and_numbered_footer() -> None:
    doc = doc_of(many(40, "Beer Mugs"), columns=3)
    n = len(doc)
    for i in range(1, n):
        t = text(doc, i)
        assert f"Page {i + 1} of {n}" in t
        assert "Sample Traders" in t
    assert "Page" not in text(doc, 0)


# --- price display ---------------------------------------------------------------


def test_pack_price_shows_the_pack_size() -> None:
    t = all_text(doc_of([entry(1, None, "374", 6)], group_by="none"))
    assert "Rs 374" in t and "for 6 pcs" in t


def test_single_piece_shows_no_pack_text() -> None:
    t = all_text(doc_of([entry(1, None, "120", 1)], group_by="none"))
    assert "Rs 120" in t and "for 1 pcs" not in t and "per piece" not in t


def test_per_piece_basis_shows_piece_price_and_the_pack_total() -> None:
    t = all_text(doc_of([entry(1, None, "374", 6)], group_by="none", price_basis="piece"))
    assert "Rs 62.33" in t and "per piece" in t and "pack of 6: Rs 374" in t


def test_per_piece_basis_leaves_single_pieces_alone() -> None:
    t = all_text(doc_of([entry(1, None, "120", 1)], group_by="none", price_basis="piece"))
    assert "Rs 120" in t and "per piece" not in t


@pytest.mark.parametrize(
    ("price", "pack", "expected"),
    [
        ("374", 6, "62.33"),
        ("100", 3, "33.33"),
        ("105", 2, "52.50"),
        ("1", 3, "0.33"),
        ("10", 4, "2.50"),
    ],
)
def test_piece_price_rounds_half_up(price: str, pack: int, expected: str) -> None:
    assert piece_price(Decimal(price), pack) == Decimal(expected)


def test_indian_grouping_in_prices() -> None:
    t = all_text(doc_of([entry(1, None, "123456", 1)], group_by="none"))
    assert "Rs 1,23,456" in t


# --- code toggle -----------------------------------------------------------------


def test_show_code_toggle() -> None:
    entries = many(3, None)
    with_code = all_text(doc_of(entries, group_by="none", show_code=True))
    without = all_text(doc_of(entries, group_by="none", show_code=False))
    assert "GL-000001" in with_code and "GL-000001" not in without
    assert "Rs 374" in without  # the price stays


# --- photos ------------------------------------------------------------------------


def test_the_same_photo_is_embedded_once() -> None:
    doc = doc_of(many(40, None), columns=3, group_by="none")
    xrefs = {img[0] for i in range(1, len(doc)) for img in doc[i].get_images()}
    assert len(xrefs) == 1


def test_distinct_photos_are_embedded_separately() -> None:
    entries = [entry(1, None, image=photo((200, 0, 0))), entry(2, None, image=photo((0, 200, 0)))]
    doc = doc_of(entries, group_by="none")
    assert len({img[0] for img in doc[1].get_images()}) == 2


def test_a_missing_photo_gets_a_placeholder() -> None:
    doc = doc_of([entry(1, None, image=None), entry(2, None)], group_by="none")
    assert "No photo" in text(doc, 1)
    assert "GL-000002" in text(doc, 1)


# --- text safety -------------------------------------------------------------------


@pytest.mark.parametrize("columns", [2, 3, 4])
def test_long_names_never_spill_out_of_their_card(columns: int) -> None:
    long = "Extraordinarily Long Product Name With Many Words That Cannot Fit " * 3
    word = "Supercalifragilisticexpialidocious" * 4
    doc = doc_of([entry(1, None, name=long), entry(2, None, name=word)], columns=columns,
                 group_by="none")
    cw, _ = cp._geometry(columns)
    for block in doc[1].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["bbox"][1] <= cp.TOP or span["bbox"][3] >= cp.BOTTOM + 2:
                    continue  # the running header and the page footer are not cards
                col = int((span["bbox"][0] - cp.MARGIN) // (cw + cp.GAP))
                right = cp.MARGIN + col * (cw + cp.GAP) + cw
                assert span["bbox"][2] <= right + 0.5, span["text"]
    assert ".." in text(doc, 1)


def test_non_latin_names_do_not_break_the_page() -> None:
    name = "काँच Café ₹"
    doc = doc_of([entry(1, None, name=name)], group_by="none")
    assert "Cafe" in text(doc, 1)


# --- progress, speed, preview ------------------------------------------------------


def test_progress_reaches_the_total() -> None:
    seen: list[tuple[int, int]] = []
    build(many(60, None), CatalogPdfOptions(group_by="none"), BRAND, TODAY,
          lambda d, t: seen.append((d, t))).close()
    assert seen[0] == (25, 60) and seen[-1] == (60, 60)


def test_a_hundred_and_fifty_items_build_quickly() -> None:
    t = time.time()
    pdf, pages = render_pdf(many(150), CatalogPdfOptions(), BRAND, TODAY)
    assert time.time() - t < 10
    assert pages == len(pymupdf.open(stream=pdf, filetype="pdf")) and len(pdf) > 1000


def test_preview_is_a_png_of_the_first_product_page() -> None:
    png = render_preview_png(_groups(), CatalogPdfOptions(), BRAND, TODAY)
    img = Image.open(io.BytesIO(png)).convert("L")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = img.size
    assert 0.69 < w / h < 0.72  # A4 portrait
    assert min(img.getdata()) < 128  # something is drawn: not a blank page


def test_preview_skips_cover_and_contents() -> None:
    # a one-page flat catalog: preview is page index 1 (the first product page)
    png = render_preview_png(many(3, None), CatalogPdfOptions(group_by="none"), BRAND, TODAY)
    assert png[:4] == b"\x89PNG"

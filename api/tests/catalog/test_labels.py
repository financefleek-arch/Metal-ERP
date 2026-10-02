"""Barcode + label renderer: pure functions, decoded back with a real barcode reader."""

from __future__ import annotations

import time
from decimal import Decimal

import pymupdf
import pytest
import zxingcpp
from PIL import Image

from app.services.catalog.barcode import QUIET_MODULES, modules, runs
from app.services.catalog.labels import (
    PRESETS,
    LabelItem,
    LabelOptions,
    inr,
    latin1,
    page_count,
    render_pdf,
    render_preview_png,
)

MM = 72 / 25.4


def decode_pdf(pdf: bytes, dpi: int = 300) -> list[tuple[int, str, tuple[float, float]]]:
    """(page index, text, centre in pt) of every barcode found in the PDF."""
    out: list[tuple[int, str, tuple[float, float]]] = []
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    scale = 72 / dpi
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        for r in zxingcpp.read_barcodes(img):
            p = r.position
            cx = (p.top_left.x + p.bottom_right.x) / 2 * scale
            cy = (p.top_left.y + p.bottom_right.y) / 2 * scale
            out.append((i, r.text, (cx, cy)))
    doc.close()
    return out


def items(n: int, name: str = "DELI 190 ML Juice Glass 6 PC") -> list[LabelItem]:
    return [LabelItem(f"GL-{i:06d}", f"{name} {i}", Decimal(100 + i)) for i in range(1, n + 1)]


# --- Code 128 modules ----------------------------------------------------------


def test_modules_start_and_stop_patterns() -> None:
    m = modules("GL-000001")
    assert set(m) <= {"0", "1"}
    assert m.endswith("1100011101011")  # Code 128 stop pattern
    assert m.startswith(("11010000100", "11010010000", "11010011100"))  # start A / B / C


def test_modules_are_cached_and_deterministic() -> None:
    assert modules("GL-000001") is modules("GL-000001")
    assert modules("GL-000001") != modules("GL-000002")


@pytest.mark.parametrize("bad", ["", "x" * 41, "GL-000é", "a\nb", "a\tb"])
def test_unprintable_or_oversize_text_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        modules(bad)


def test_runs_cover_exactly_the_bars() -> None:
    m = modules("GL-000123")
    rs = runs(m)
    assert sum(length for _, length in rs) == m.count("1")
    assert all(m[s : s + n] == "1" * n for s, n in rs)
    # runs never touch: a space always separates them
    assert all(a[0] + a[1] < b[0] for a, b in zip(rs, rs[1:], strict=False))


# --- presets -------------------------------------------------------------------


def test_preset_sizes_are_exact_physical_sizes() -> None:
    for key, (w, h) in {
        "roll_50x25": (50, 25),
        "roll_38x25": (38, 25),
        "sheet_a4_3x8": (210, 297),
    }.items():
        pdf, _ = render_pdf(items(1), key, LabelOptions())
        page = pymupdf.open(stream=pdf, filetype="pdf")[0]
        assert page.rect.width / MM == pytest.approx(w, abs=0.1)
        assert page.rect.height / MM == pytest.approx(h, abs=0.1)


def test_a4_sheet_is_24_up_with_70_by_37_labels() -> None:
    p = PRESETS["sheet_a4_3x8"]
    assert p.per_page == 24
    assert p.label_w_pt / MM == pytest.approx(70, abs=0.01)
    assert p.label_h_pt / MM == pytest.approx(37.1, abs=0.1)


# --- every barcode decodes back to its code ------------------------------------


@pytest.mark.parametrize("preset", list(PRESETS))
@pytest.mark.parametrize(
    "opts",
    [
        LabelOptions(),
        LabelOptions(show_price=True),
        LabelOptions(show_name=False),
        LabelOptions(show_name=False, show_code=False),
    ],
    ids=["default", "price", "no-name", "bars-only"],
)
def test_every_barcode_decodes_to_its_own_code(preset: str, opts: LabelOptions) -> None:
    its = items(30)
    pdf, ok = render_pdf(its, preset, opts)
    assert ok
    got = [t for _, t, _ in decode_pdf(pdf)]
    assert sorted(got) == sorted(i.code for i in its)


def test_a_long_prefix_still_decodes_on_the_widest_label() -> None:
    its = [LabelItem("ABCDEFGH-000042", "Long prefix item", Decimal(10))]
    pdf, _ = render_pdf(its, "sheet_a4_3x8", LabelOptions())
    assert [t for _, t, _ in decode_pdf(pdf)] == ["ABCDEFGH-000042"]


def test_dense_barcodes_are_flagged_as_hard_to_scan() -> None:
    long_code = [LabelItem("ABCDEFGH-000042", "x", Decimal(1))]
    _, ok = render_pdf(long_code, "roll_38x25", LabelOptions())
    assert ok is False
    _, ok = render_pdf(items(1), "roll_38x25", LabelOptions())
    assert ok is True


# --- copies, pages, sheet slots ------------------------------------------------


def test_copies_repeat_each_label_in_order() -> None:
    pdf, _ = render_pdf(items(3), "roll_50x25", LabelOptions(copies=2))
    got = [t for _, t, _ in sorted(decode_pdf(pdf), key=lambda x: x[0])]
    assert got == ["GL-000001", "GL-000001", "GL-000002", "GL-000002", "GL-000003", "GL-000003"]


@pytest.mark.parametrize(
    ("n", "copies", "start_at", "pages"),
    [(30, 1, 1, 2), (24, 1, 1, 1), (25, 1, 1, 2), (24, 1, 2, 2), (10, 3, 1, 2), (1, 1, 24, 1)],
)
def test_sheet_page_count(n: int, copies: int, start_at: int, pages: int) -> None:
    opts = LabelOptions(copies=copies, start_at=start_at)
    assert page_count(n * copies, PRESETS["sheet_a4_3x8"], opts) == pages
    pdf, _ = render_pdf(items(n), "sheet_a4_3x8", opts)
    assert len(pymupdf.open(stream=pdf, filetype="pdf")) == pages


def test_roll_is_one_label_per_page() -> None:
    assert page_count(7, PRESETS["roll_50x25"], LabelOptions(start_at=9)) == 7  # start_at ignored


def test_sheet_labels_land_in_row_major_slots() -> None:
    p = PRESETS["sheet_a4_3x8"]
    pdf, _ = render_pdf(items(5), "sheet_a4_3x8", LabelOptions())
    for _, text, (cx, cy) in decode_pdf(pdf):
        slot = int(text[-6:]) - 1
        row, col = divmod(slot, 3)
        assert col * p.label_w_pt < cx < (col + 1) * p.label_w_pt
        assert row * p.label_h_pt < cy < (row + 1) * p.label_h_pt


def test_start_at_skips_used_slots_and_continues_on_the_next_sheet() -> None:
    p = PRESETS["sheet_a4_3x8"]
    pdf, _ = render_pdf(items(3), "sheet_a4_3x8", LabelOptions(start_at=23))
    found = {t: (pg, cx, cy) for pg, t, (cx, cy) in decode_pdf(pdf)}
    assert found["GL-000001"][0] == 0 and found["GL-000002"][0] == 0  # slots 23, 24
    assert found["GL-000003"][0] == 1  # spills onto the second sheet, slot 1
    pg, cx, cy = found["GL-000003"]
    assert cx < p.label_w_pt and cy < p.label_h_pt
    _, cx, cy = found["GL-000001"]  # slot 23 = row 7, col 1... (22 // 3, 22 % 3)
    assert p.label_w_pt < cx < 2 * p.label_w_pt and 7 * p.label_h_pt < cy < 8 * p.label_h_pt


def test_start_at_beyond_the_sheet_wraps_to_a_valid_slot() -> None:
    pdf, _ = render_pdf(items(2), "sheet_a4_3x8", LabelOptions(start_at=24))
    assert len(decode_pdf(pdf)) == 2


# --- text ------------------------------------------------------------------------


def _page_text(pdf: bytes) -> str:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return " ".join(p.get_text() for p in doc)


def test_text_toggles() -> None:
    it = [LabelItem("GL-000007", "Beer Mug 480 ML", Decimal("1256"))]
    full, _ = render_pdf(it, "roll_50x25", LabelOptions(show_price=True))
    text = _page_text(full)
    assert "Beer Mug 480 ML" in text and "GL-000007" in text and "Rs 1,256" in text

    bare, _ = render_pdf(it, "roll_50x25", LabelOptions(show_name=False, show_code=False))
    assert _page_text(bare).strip() == ""

    no_price, _ = render_pdf(it, "roll_50x25", LabelOptions())
    assert "Rs" not in _page_text(no_price)


def test_price_without_a_price_value_is_simply_omitted() -> None:
    it = [LabelItem("GL-000001", "No price item", None)]
    pdf, _ = render_pdf(it, "roll_50x25", LabelOptions(show_price=True, show_code=False))
    assert "Rs" not in _page_text(pdf)
    assert len(decode_pdf(pdf)) == 1


@pytest.mark.parametrize("preset", list(PRESETS))
def test_long_names_never_spill_outside_the_label(preset: str) -> None:
    name = "Extraordinarily Long Product Name With Many Words That Cannot Possibly Fit " * 3
    word = "Supercalifragilisticexpialidocious" * 4
    its = [LabelItem("GL-000001", name, Decimal(5)), LabelItem("GL-000002", word, Decimal(5))]
    pdf, _ = render_pdf(its, preset, LabelOptions(show_price=True))
    p = PRESETS[preset]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    x0, y0, x1, y1 = span["bbox"]
                    col = int(x0 // p.label_w_pt)
                    # right edge stays inside the same label cell
                    assert x1 <= (col + 1) * p.label_w_pt + 0.5, span["text"]
                    assert y1 <= page.rect.height + 0.5
    assert len(decode_pdf(pdf)) == 2  # and the barcodes are untouched by the long text


def test_long_name_is_cut_with_an_ellipsis() -> None:
    it = [LabelItem("GL-000001", "word " * 60, Decimal(5))]
    pdf, _ = render_pdf(it, "roll_38x25", LabelOptions())
    assert ".." in _page_text(pdf)


def test_non_latin_names_do_not_break_rendering() -> None:
    name = "काँच गिलास Café ₹"
    it = [LabelItem("GL-000001", name, None)]
    pdf, _ = render_pdf(it, "roll_50x25", LabelOptions())
    assert "Cafe" in _page_text(pdf)  # accents folded, Devanagari and the rupee sign dropped
    assert len(decode_pdf(pdf)) == 1


def test_latin1_helper() -> None:
    assert latin1("Café  au\tlait") == "Cafe au lait"
    assert latin1("काँच") == ""
    assert latin1("A ₹ B") == "A B"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("1256", "Rs 1,256"),
        ("99", "Rs 99"),
        ("99.5", "Rs 99.50"),
        ("1000", "Rs 1,000"),
        ("100000", "Rs 1,00,000"),
        ("1256000", "Rs 12,56,000"),
        ("0", "Rs 0"),
    ],
)
def test_inr_uses_indian_grouping(value: str, text: str) -> None:
    assert inr(Decimal(value)) == text


# --- preview + speed -------------------------------------------------------------


def test_preview_png_has_the_label_proportions() -> None:
    import io

    for key, ratio in (("roll_50x25", 2.0), ("roll_38x25", 1.52), ("sheet_a4_3x8", 70 / 37.125)):
        png = render_preview_png(items(1)[0], key, LabelOptions())
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = Image.open(io.BytesIO(png)).size
        assert w / h == pytest.approx(ratio, rel=0.02)


def test_preview_barcode_decodes() -> None:
    import io

    png = render_preview_png(items(1)[0], "roll_50x25", LabelOptions(), dpi=300)
    res = zxingcpp.read_barcodes(Image.open(io.BytesIO(png)).convert("RGB"))
    assert [r.text for r in res] == ["GL-000001"]


def test_two_hundred_labels_render_quickly() -> None:
    t = time.time()
    pdf, ok = render_pdf(items(200), "roll_50x25", LabelOptions(show_price=True))
    assert time.time() - t < 8
    assert ok and len(pymupdf.open(stream=pdf, filetype="pdf")) == 200


def test_progress_callback_reports_up_to_the_total() -> None:
    seen: list[tuple[int, int]] = []
    render_pdf(items(60), "roll_50x25", LabelOptions(), lambda d, t: seen.append((d, t)))
    assert seen[0] == (25, 60) and seen[-1] == (60, 60)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_quiet_zone_constant_matches_the_spec() -> None:
    assert QUIET_MODULES == 10

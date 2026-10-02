"""Barcode label PDFs.

Presets are exact physical sizes, so the PDF prints 1:1 on a roll printer or a label
sheet ("print at 100% / actual size"). Roll presets are one label per page; the A4
sheet preset tiles 3 x 8 labels per page and can start part-way down (to reuse a
partly used sheet).

Drawn with pymupdf (already a dependency; WeasyPrint does not load on the dev
Windows box). Text uses the built-in Helvetica, which has no rupee sign, so prices
read "Rs 1,256" and names are reduced to Latin-1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import pymupdf

from app.services.catalog.barcode import QUIET_MODULES, modules, runs
from app.services.catalog.pdf_text import inr, latin1
from app.services.catalog.pdf_text import put_text as _text
from app.services.catalog.pdf_text import wrap as _wrap

_PT_PER_MM = 72 / 25.4

# Below this bar width (pt) a cheap scanner starts to struggle (0.25 mm).
_MIN_MODULE_PT = 0.25 * _PT_PER_MM

@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    page_w_mm: float
    page_h_mm: float
    cols: int  # labels across the page (1 for a roll)
    rows: int  # labels down the page (1 for a roll)

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    @property
    def label_w_pt(self) -> float:
        return self.page_w_mm * _PT_PER_MM / self.cols

    @property
    def label_h_pt(self) -> float:
        return self.page_h_mm * _PT_PER_MM / self.rows


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset("roll_50x25", "Roll 50 x 25 mm", 50, 25, 1, 1),
        Preset("roll_38x25", "Roll 38 x 25 mm", 38, 25, 1, 1),
        Preset("sheet_a4_3x8", "A4 sheet, 3 x 8 (70 x 37 mm)", 210, 297, 3, 8),
    )
}


@dataclass(frozen=True)
class LabelItem:
    code: str
    name: str
    price: Decimal | None = None


@dataclass(frozen=True)
class LabelOptions:
    show_name: bool = True
    show_code: bool = True
    show_price: bool = False
    copies: int = 1
    start_at: int = 1  # first slot to use on the first sheet (1-based); sheets only


def draw_label(
    page: pymupdf.Page,
    shape: pymupdf.Shape,
    x: float,
    y: float,
    w: float,
    h: float,
    item: LabelItem,
    opts: LabelOptions,
) -> bool:
    """Draw one label in the box (x, y, w, h). Bars go on `shape` (batched per page).

    Returns False when the bars come out thinner than a scanner can read reliably.
    """
    k = max(0.6, min(w / 141.7, h / 70.9))  # scale from the 50 x 25 mm design
    pad = 4 * k
    inner_w = w - 2 * pad

    cur = y + 3.5 * k
    if opts.show_name:
        size = 5.6 * k
        lines = _wrap(latin1(item.name) or item.code, size, inner_w, 2)
        for i, ln in enumerate(lines):
            _text(page, x + pad, cur + size + i * 6.4 * k, ln, size)
        cur += 2 * 6.4 * k + 2 * k  # always reserve two lines so barcodes align across labels

    show_bottom = opts.show_code or (opts.show_price and item.price is not None)
    bottom_h = (8 * k + 2 * k) if show_bottom else 0.0
    bar_top = cur
    bar_bottom = y + h - 3 * k - bottom_h - (1.5 * k if show_bottom else 0.0)
    bar_h = max(bar_bottom - bar_top, 10.0)

    pattern = modules(item.code)
    n = len(pattern)
    module_w = (w - 2 * pad) / (n + 2 * QUIET_MODULES)
    bars_w = module_w * n
    bx = x + (w - bars_w) / 2
    for start, length in runs(pattern):
        left = bx + start * module_w
        shape.draw_rect(pymupdf.Rect(left, bar_top, left + length * module_w, bar_top + bar_h))

    if show_bottom:
        base = y + h - 3 * k
        if opts.show_code:
            _text(page, x + pad, base, item.code, 6.5 * k, bold=True)
        if opts.show_price and item.price is not None:
            _text(page, x + pad, base, inr(item.price), 8 * k, bold=True, align="r", width=inner_w)
    return module_w >= _MIN_MODULE_PT


def label_count(n_items: int, opts: LabelOptions) -> int:
    return n_items * opts.copies


def page_count(n_labels: int, preset: Preset, opts: LabelOptions) -> int:
    if preset.per_page == 1:
        return n_labels
    skip = (opts.start_at - 1) % preset.per_page
    return -(-(skip + n_labels) // preset.per_page)  # ceil


def render_pdf(
    items: list[LabelItem],
    preset_key: str,
    opts: LabelOptions,
    progress: Callable[[int, int], None] | None = None,
    title: str = "Labels",
) -> tuple[bytes, bool]:
    """The label PDF, and whether every barcode is wide enough to scan reliably."""
    preset = PRESETS[preset_key]
    labels = [it for it in items for _ in range(opts.copies)]
    total = len(labels)
    doc = pymupdf.open()
    page_w, page_h = preset.page_w_mm * _PT_PER_MM, preset.page_h_mm * _PT_PER_MM
    lw, lh = preset.label_w_pt, preset.label_h_pt
    ok = True

    page: pymupdf.Page | None = None
    shape: pymupdf.Shape | None = None

    def close_page() -> None:
        if shape is not None:
            shape.finish(color=None, fill=(0, 0, 0))
            shape.commit()

    slot = (opts.start_at - 1) % preset.per_page if preset.per_page > 1 else 0
    for i, item in enumerate(labels):
        if page is None or slot >= preset.per_page or preset.per_page == 1:
            close_page()
            page = doc.new_page(width=page_w, height=page_h)
            shape = page.new_shape()
            if slot >= preset.per_page:
                slot = 0
        assert shape is not None
        row, col = divmod(slot, preset.cols)
        if not draw_label(page, shape, col * lw, row * lh, lw, lh, item, opts):
            ok = False
        slot += 1
        if progress is not None and (i + 1) % 25 == 0:
            progress(i + 1, total)
    close_page()
    doc.set_metadata({"title": title})
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    if progress is not None:
        progress(total, total)
    return data, ok


def render_preview_png(
    item: LabelItem, preset_key: str, opts: LabelOptions, dpi: int = 220
) -> bytes:
    """One label at its true proportion, as PNG (used by the dialog preview)."""
    preset = PRESETS[preset_key]
    doc = pymupdf.open()
    page = doc.new_page(width=preset.label_w_pt, height=preset.label_h_pt)
    shape = page.new_shape()
    draw_label(page, shape, 0, 0, preset.label_w_pt, preset.label_h_pt, item, opts)
    shape.finish(color=None, fill=(0, 0, 0))
    shape.commit()
    pix = page.get_pixmap(dpi=dpi)
    png: bytes = pix.tobytes("png")
    doc.close()
    return png

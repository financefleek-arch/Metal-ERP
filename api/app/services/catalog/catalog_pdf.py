"""The customer-facing catalog PDF: the shop's prices, never the supplier's.

Fixed layout (A4 portrait): a cover, an optional contents page, then a product grid in
2, 3 or 4 columns. Each card shows the photo, name, our item code (optional) and the
new price. Cost prices and the supplier's own code never reach this module: entries
carry only what may be printed.

Sections (groups) flow one after another with a heading band, so small groups do not
each waste a page; a section that runs onto the next page gets a "continued" heading.
Every distinct photo is embedded once, however many times it appears.

The cover is automatic for now (firm name, title, item count, date, contact lines);
cover customisation is on the backlog.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Literal

import pymupdf

from app.services.catalog.pdf_text import inr, latin1, put_text, wrap

PAGE_W, PAGE_H = 595.28, 841.89  # A4, points
MARGIN = 28.0
GAP = 10.0
TOP = 40.0  # product content starts below the running header
BOTTOM = PAGE_H - 34.0  # and ends above the footer
HEADING_H = 22.0
SECTION_GAP = 8.0
PHOTO_RATIO = 262 / 388  # the supplier photos' height / width

ACCENT = (0.141, 0.341, 0.443)
ACCENT_SOFT = (0.89, 0.93, 0.95)
MUTED = (0.42, 0.42, 0.42)
LINE = (0.84, 0.84, 0.84)

Columns = Literal[2, 3, 4]
GroupBy = Literal["group", "none"]
PriceBasis = Literal["pack", "piece"]

_TOC_LINE_H = 18.0
_TOC_TOP = 120.0


@dataclass(frozen=True)
class CatalogEntry:
    """One printable product. Deliberately has no cost price and no supplier code."""

    code: str
    name: str
    group: str | None
    price: Decimal  # our new price for one pack
    pack_qty: int
    image: bytes | None


@dataclass(frozen=True)
class CatalogPdfOptions:
    columns: int = 3
    group_by: str = "group"
    show_code: bool = True
    price_basis: str = "pack"
    contents: bool = True


@dataclass(frozen=True)
class Brand:
    """Printed on the cover. All from the firm profile."""

    firm_name: str
    title: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class _Metrics:
    name_size: float
    code_size: float
    price_size: float
    small_size: float


_METRICS = {
    2: _Metrics(10.5, 8.0, 16.0, 8.5),
    3: _Metrics(8.6, 7.0, 12.5, 7.2),
    4: _Metrics(7.4, 6.3, 10.5, 6.4),
}

OTHER_GROUP = "Other"

# One placed element on a product page.
_Heading = tuple[Literal["heading"], float, str, int, bool]  # y, name, count, continued
_Cell = tuple[Literal["cell"], float, float, CatalogEntry]  # x, y, entry
_Placed = _Heading | _Cell


def piece_price(price: Decimal, pack_qty: int) -> Decimal:
    return (price / max(pack_qty, 1)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _geometry(columns: int) -> tuple[float, float]:
    """(card width, card height) for a column count."""
    m = _METRICS[columns]
    cw = (PAGE_W - 2 * MARGIN - (columns - 1) * GAP) / columns
    ih = cw * PHOTO_RATIO
    ch = ih + 6 + 2 * m.name_size * 1.2 + 8 + m.small_size + m.price_size + 8
    return cw, ch


def _sections(
    entries: list[CatalogEntry], group_by: str
) -> list[tuple[str | None, list[CatalogEntry]]]:
    if group_by != "group":
        return [(None, list(entries))]
    named: dict[str, list[CatalogEntry]] = {}
    other: list[CatalogEntry] = []
    for e in entries:
        if e.group:
            named.setdefault(e.group, []).append(e)
        else:
            other.append(e)
    out: list[tuple[str | None, list[CatalogEntry]]] = [
        (name, named[name]) for name in sorted(named, key=str.casefold)
    ]
    if other:
        out.append((OTHER_GROUP, other))
    return out


def _plan(
    sections: list[tuple[str | None, list[CatalogEntry]]], columns: int
) -> tuple[list[list[_Placed]], dict[str, int]]:
    """Lay the sections out over pages. Returns the pages' contents and, for each section
    name, the (0-based) product page where it starts."""
    cw, ch = _geometry(columns)
    pages: list[list[_Placed]] = [[]]
    starts: dict[str, int] = {}
    y = TOP

    def new_page() -> None:
        nonlocal y
        pages.append([])
        y = TOP

    for name, items in sections:
        if name is not None:
            if y + HEADING_H + GAP + ch > BOTTOM:
                new_page()
            starts[name] = len(pages) - 1
            pages[-1].append(("heading", y, name, len(items), False))
            y += HEADING_H + GAP
        col = 0
        for entry in items:
            if col == 0 and y + ch > BOTTOM:
                new_page()
                if name is not None:
                    pages[-1].append(("heading", y, name, len(items), True))
                    y += HEADING_H + GAP
            pages[-1].append(("cell", MARGIN + col * (cw + GAP), y, entry))
            col += 1
            if col == columns:
                col = 0
                y += ch + GAP
        if col != 0:
            y += ch + GAP
        y += SECTION_GAP
    return pages, starts


def toc_page_count(n_sections: int) -> int:
    per_page = int((PAGE_H - _TOC_TOP - MARGIN) // _TOC_LINE_H)
    return max(1, -(-n_sections // per_page))


def _cover(doc: pymupdf.Document, brand: Brand, n_items: int, today: date) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(pymupdf.Rect(0, 0, PAGE_W, 330), color=None, fill=ACCENT)
    firm = latin1(brand.firm_name).upper() or "CATALOG"
    put_text(page, 44, 150, firm[:46], 13, bold=True, color=(0.86, 0.93, 0.96))
    size = 34.0
    title_lines = wrap(latin1(brand.title) or "Price List", size, PAGE_W - 88, 3, bold=True)
    for i, line in enumerate(title_lines):
        put_text(page, 44, 205 + i * 40, line, size, bold=True, color=(1, 1, 1))
    put_text(
        page, 44, 312, f"{n_items:,} items  |  {today.day} {today:%b %Y}", 12,
        color=(0.86, 0.93, 0.96),
    )
    y = 380.0
    for text in (brand.address, brand.phone, brand.email):
        if text:
            put_text(page, 44, y, latin1(text)[:80], 11, color=MUTED)
            y += 18


def _toc(
    doc: pymupdf.Document, starts: dict[str, int], counts: dict[str, int], first_product_page: int
) -> None:
    names = list(starts)
    per_page = int((PAGE_H - _TOC_TOP - MARGIN) // _TOC_LINE_H)
    for p in range(toc_page_count(len(names))):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        put_text(page, MARGIN, 70, "Contents", 20, bold=True, color=ACCENT)
        page.draw_line((MARGIN, 82), (PAGE_W - MARGIN, 82), color=LINE, width=0.8)
        y = _TOC_TOP
        for name in names[p * per_page : (p + 1) * per_page]:
            put_text(page, MARGIN, y, latin1(name), 11)
            put_text(
                page, MARGIN, y, f"{counts[name]} items", 9, align="r",
                width=PAGE_W - 2 * MARGIN - 60, color=MUTED,
            )
            put_text(
                page, MARGIN, y, str(first_product_page + starts[name]), 11, bold=True,
                align="r", width=PAGE_W - 2 * MARGIN,
            )
            y += _TOC_LINE_H


def _draw_cell(
    page: pymupdf.Page,
    x: float,
    y: float,
    entry: CatalogEntry,
    opts: CatalogPdfOptions,
    xrefs: dict[str, int],
) -> None:
    cols = opts.columns
    m = _METRICS[cols]
    cw, ch = _geometry(cols)
    ih = cw * PHOTO_RATIO
    page.draw_rect(pymupdf.Rect(x, y, x + cw, y + ch), color=LINE, fill=None, width=0.5)

    box = pymupdf.Rect(x + 1, y + 1, x + cw - 1, y + ih)
    if entry.image:
        key = sha256(entry.image).hexdigest()
        if key in xrefs:
            page.insert_image(box, xref=xrefs[key])
        else:
            xrefs[key] = page.insert_image(box, stream=entry.image)
    else:
        page.draw_rect(box, color=None, fill=(0.95, 0.95, 0.95))
        put_text(page, box.x0, box.y0 + ih / 2, "No photo", m.small_size, align="c",
                 width=box.width, color=MUTED)

    ty = y + ih + 6 + m.name_size
    for line in wrap(latin1(entry.name) or entry.code, m.name_size, cw - 10, 2):
        put_text(page, x + 5, ty, line, m.name_size)
        ty += m.name_size * 1.2

    base = y + ch - 7
    if opts.price_basis == "piece" and entry.pack_qty > 1:
        shown = inr(piece_price(entry.price, entry.pack_qty))
        sub = f"per piece  (pack of {entry.pack_qty}: {inr(entry.price)})"
    else:
        shown = inr(entry.price)
        sub = f"for {entry.pack_qty} pcs" if entry.pack_qty > 1 else ""
    put_text(page, x + 5, base, shown, m.price_size, bold=True, align="r", width=cw - 10,
             color=ACCENT)
    if sub:
        put_text(page, x + 5, base - m.price_size - 1, sub, m.small_size, align="r",
                 width=cw - 10, color=MUTED)
    if opts.show_code:
        put_text(page, x + 5, base, entry.code, m.code_size, color=MUTED)


def _draw_heading(page: pymupdf.Page, y: float, name: str, count: int, cont: bool) -> None:
    page.draw_rect(
        pymupdf.Rect(MARGIN, y, PAGE_W - MARGIN, y + HEADING_H), color=None, fill=ACCENT_SOFT
    )
    label = latin1(name) + ("  (continued)" if cont else "")
    put_text(page, MARGIN + 8, y + 15, label, 11, bold=True, color=ACCENT)
    put_text(page, MARGIN, y + 15, f"{count} items", 8.5, align="r",
             width=PAGE_W - 2 * MARGIN - 8, color=MUTED)


def _frame(page: pymupdf.Page, brand: Brand, number: int, total: int) -> None:
    put_text(page, MARGIN, 24, latin1(brand.firm_name)[:60], 9, bold=True, color=ACCENT)
    put_text(page, MARGIN, 24, latin1(brand.title)[:60], 9, align="r",
             width=PAGE_W - 2 * MARGIN, color=MUTED)
    page.draw_line((MARGIN, 30), (PAGE_W - MARGIN, 30), color=LINE, width=0.6)
    put_text(page, 0, PAGE_H - 16, f"Page {number} of {total}", 8, align="c", width=PAGE_W,
             color=MUTED)


def build(
    entries: list[CatalogEntry],
    opts: CatalogPdfOptions,
    brand: Brand,
    generated_on: date,
    progress: Callable[[int, int], None] | None = None,
) -> pymupdf.Document:
    """The catalog as an open document (caller closes it)."""
    if opts.columns not in _METRICS:
        raise ValueError("columns must be 2, 3 or 4")
    sections = _sections(entries, opts.group_by)
    pages, starts = _plan(sections, opts.columns)
    counts = {name: len(items) for name, items in sections if name is not None}
    with_toc = opts.contents and opts.group_by == "group" and len(starts) >= 2
    toc_pages = toc_page_count(len(starts)) if with_toc else 0
    first_product_page = 1 + toc_pages + 1  # 1-based number of the first product page

    doc = pymupdf.open()
    _cover(doc, brand, len(entries), generated_on)
    if with_toc:
        _toc(doc, starts, counts, first_product_page)

    xrefs: dict[str, int] = {}
    done = 0
    total = len(entries)
    for placed in pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        for item in placed:
            if item[0] == "heading":
                _, y, name, count, cont = item
                _draw_heading(page, y, name, count, cont)
            else:
                _, x, y, entry = item
                _draw_cell(page, x, y, entry, opts, xrefs)
                done += 1
                if progress is not None and done % 25 == 0:
                    progress(done, total)
    n_pages = len(doc)
    for i in range(1, n_pages):  # the cover carries no frame
        _frame(doc[i], brand, i + 1, n_pages)
    if progress is not None:
        progress(total, total)
    doc.set_metadata({"title": latin1(brand.title) or "Catalog"})
    return doc


def render_pdf(
    entries: list[CatalogEntry],
    opts: CatalogPdfOptions,
    brand: Brand,
    generated_on: date,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[bytes, int]:
    """(PDF bytes, page count)."""
    doc = build(entries, opts, brand, generated_on, progress)
    pages = len(doc)
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, pages


def render_preview_png(
    entries: list[CatalogEntry],
    opts: CatalogPdfOptions,
    brand: Brand,
    generated_on: date,
    dpi: int = 70,
) -> bytes:
    """The first product page (cover and contents skipped), as PNG."""
    doc = build(entries, opts, brand, generated_on)
    sections = _sections(entries, opts.group_by)
    n_sections = sum(1 for name, _ in sections if name is not None)
    with_toc = opts.contents and opts.group_by == "group" and n_sections >= 2
    index = 1 + (toc_page_count(n_sections) if with_toc else 0)
    png: bytes = doc[min(index, len(doc) - 1)].get_pixmap(dpi=dpi).tobytes("png")
    doc.close()
    return png

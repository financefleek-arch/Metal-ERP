"""Read a supplier catalog PDF into product cells.

Targets the common "catalog grid" export: each product is one embedded photo
with its text underneath, a price line (`Rs 190 for 6 pcs`), the name (1-3 wrapped
lines), and the supplier's product code as the last line. Deterministic, no LLM;
the POC parsed all 2,056 items of a 172-page file in ~3 s.

Two PyMuPDF facts this depends on (see the execution plan, section 3):
  * images come from `page.get_text("dict")` image blocks, which carry the bytes
    and bbox. `page.get_image_info()` costs ~4 s/page on Word-made catalogs
    (every page lists every image in its resources) and must not be used.
  * words are grouped into lines by their y position, not by PDF text block:
    "Rs 190" and "for 6 pcs" can sit in different blocks on the same line.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pymupdf

# Photos smaller than this (pt) are logos/icons, not product photos.
_MIN_PHOTO_W = 60.0
_MIN_PHOTO_H = 40.0
# Column band: the photo's x-range plus a little slack on each side.
_BAND_SLACK = 6.0
# Text under a photo is searched down to the next photo in the column, but never
# further than this many pt or one photo height (whichever is larger).
_MIN_TEXT_REACH = 110.0

_PRICE = re.compile(
    r"(?:Rs\.?|₹|INR)\s*(?P<price>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?:for|/)\s*(?P<pack>\d+)\s*(?:pcs?|pieces?|nos|sets?)?)?",
    re.IGNORECASE,
)
_CODE_LINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-+./_()#]*$")
_MAX_CODE_LEN = 40
# Words on one text line further apart than this (pt) are separate segments: a
# wrapped name word ('CTN') can sit on the same line as the left-aligned code.
_SEGMENT_GAP = 18.0
_MAX_WARNINGS = 200


@dataclass
class Cell:
    page_no: int
    position: int  # reading order within the page, 1-based
    supplier_code: str
    name_raw: str
    cost_price: Decimal
    pack_qty: int
    image: bytes | None  # dropped (None) once the importer has stored the photo
    image_ext: str
    image_w: int
    image_h: int
    # set by the importer when it stores the photo
    image_key: str | None = None
    image_sha256: str | None = None
    image_flag: str | None = None


@dataclass
class ExtractResult:
    page_count: int = 0
    cells: list[Cell] = field(default_factory=list)
    skipped_no_price: int = 0  # photos with no price line (blank filler cells, logos)
    # price lines ('for 6 pcs') on pages that no product photo claimed: products with no picture
    unread_price_lines: int = 0
    warnings: list[str] = field(default_factory=list)


class NotACatalog(Exception):
    """The PDF could not be opened, or no product cells were found."""


def _open(pdf: bytes | str | os.PathLike[str]) -> pymupdf.Document:
    try:
        if isinstance(pdf, bytes):
            return pymupdf.open(stream=pdf, filetype="pdf")
        return pymupdf.open(pdf)  # a path: pages are read from disk as they are needed
    except Exception as exc:  # corrupt / not a PDF
        raise NotACatalog(f"Could not open the PDF: {exc}") from exc


def extract_pages(
    pdf: bytes | str | os.PathLike[str], out: ExtractResult
) -> Iterator[list[Cell]]:
    """Yield the product cells of one page at a time; `out` collects the counts and warnings.

    Memory stays at one page's photos however big the file is: nothing is kept across pages
    except the counters, so a caller that stores each page's photos before asking for the next
    one never holds more than a page. Raises NotACatalog (at the end) when no page had a product.
    """
    doc = _open(pdf)
    seen_codes: dict[str, int] = defaultdict(int)
    total = 0

    with doc:
        out.page_count = len(doc)
        for page_idx in range(len(doc)):
            page_no = page_idx + 1
            cells: list[Cell] = []
            try:
                page = doc[page_idx]
                photos = [
                    b
                    for b in page.get_text("dict")["blocks"]
                    if b["type"] == 1
                    and (b["bbox"][2] - b["bbox"][0]) >= _MIN_PHOTO_W
                    and (b["bbox"][3] - b["bbox"][1]) >= _MIN_PHOTO_H
                ]
                if not photos:
                    continue
                words = page.get_text("words")
                page_h = page.rect.height

                # reading order: rows top to bottom, then left to right
                photos.sort(key=lambda b: (round(b["bbox"][1] / 40), b["bbox"][0]))

                for pos, b in enumerate(photos, start=1):
                    segs = _cell_lines(words, photos, b, page_h)
                    parsed = _parse_cell(segs)
                    if parsed is None:
                        out.skipped_no_price += 1
                        continue
                    price, pack, rest = parsed
                    code, name_raw = _split_code(rest)
                    if code is None:
                        code = f"P{page_no}-{pos}"
                        _warn(out, f"page {page_no} cell {pos}: no product code line; used {code}")
                    seen_codes[code] += 1
                    if seen_codes[code] > 1:
                        dup = f"{code}~{seen_codes[code]}"
                        _warn(out, f"page {page_no} cell {pos}: code {code} repeated; kept as {dup}")
                        code = dup
                    cells.append(
                        Cell(
                            page_no=page_no,
                            position=pos,
                            supplier_code=code[:60],
                            name_raw=(name_raw or code)[:400],
                            cost_price=price,
                            pack_qty=pack,
                            image=b["image"],
                            image_ext=(b.get("ext") or "jpeg").lower(),
                            image_w=int(b["width"]),
                            image_h=int(b["height"]),
                        )
                    )
                # price lines on this page beyond the products we read: products with no picture
                lines_on_page = _count_pack_lines(words)
                out.unread_price_lines += max(0, lines_on_page - len(cells))
                total += len(cells)
            finally:
                # drop this page's decoded data before the next one: MuPDF caches it otherwise
                page = photos = words = None  # noqa: F841
                pymupdf.TOOLS.store_shrink(100)
            if cells:
                yield cells
            cells = []

    if total == 0:
        raise NotACatalog(
            "No products found. This PDF's layout isn't recognised: each product needs a "
            "photo with a price line (for example 'Rs 190 for 6 pcs') under it."
        )


def extract(pdf: bytes | str | os.PathLike[str]) -> ExtractResult:
    """The whole file at once, photos included. Only for small files and tests: the importer
    reads page by page through `extract_pages` instead."""
    out = ExtractResult()
    for cells in extract_pages(pdf, out):
        out.cells.extend(cells)
    return out


def _warn(out: ExtractResult, message: str) -> None:
    if len(out.warnings) < _MAX_WARNINGS:  # a 4,000-product file must not grow this without end
        out.warnings.append(message)


def _cell_lines(words: list, photos: list[dict], photo: dict, page_h: float) -> list[list[str]]:
    """Text lines under `photo`, in its column, down to the next photo below.

    Each line is a list of segments (runs of words), split where the horizontal gap
    is wide.
    """
    x0, y0, x1, y1 = photo["bbox"]
    left, right = x0 - _BAND_SLACK, x1 + _BAND_SLACK
    reach = y1 + max(_MIN_TEXT_REACH, y1 - y0)
    for other in photos:
        ox0, oy0, ox1, _ = other["bbox"]
        if other is photo or oy0 <= y1 + 2:
            continue
        if ox1 > left and ox0 < right:  # overlaps the column band
            reach = min(reach, oy0 - 1)
    reach = min(reach, page_h)

    in_cell = [w for w in words if left <= w[0] and w[2] <= right + 2 and y1 - 2 <= w[1] <= reach]
    by_line: dict[int, list] = defaultdict(list)
    for w in in_cell:
        by_line[round(w[1] / 4)].append(w)
    return [_segments(ws) for _, ws in sorted(by_line.items())]


def _segments(line_words: list) -> list[str]:
    ws = sorted(line_words, key=lambda w: w[0])
    segs: list[list[str]] = [[ws[0][4]]]
    for prev, cur in zip(ws, ws[1:], strict=False):
        if cur[0] - prev[2] > _SEGMENT_GAP:
            segs.append([])
        segs[-1].append(cur[4])
    return [" ".join(s) for s in segs]


def _parse_cell(lines: list[list[str]]) -> tuple[Decimal, int, list[list[str]]] | None:
    """(price, pack_qty, lines after the price line), or None if no price line."""
    for i, segs in enumerate(lines):
        m = _PRICE.search(" ".join(segs))
        if m is None:
            continue
        try:
            price = Decimal(m.group("price").replace(",", ""))
        except InvalidOperation:
            continue
        pack = int(m.group("pack")) if m.group("pack") else 1
        return price, max(pack, 1), lines[i + 1 :]
    return None


def _split_code(lines: list[list[str]]) -> tuple[str | None, str]:
    """The supplier code is the left-most segment of the last line; the rest is the name.

    A code can have a stray space after a hyphen ("TXZ-02- L4-HA"): normalised away.
    Anything right of it on that line is a wrapped name word and stays in the name.
    """
    if not lines:
        return None, ""
    last = lines[-1]
    cand = re.sub(r"-\s+", "-", last[0].strip())
    tail = last[1:]
    if len(cand) <= _MAX_CODE_LEN and _CODE_LINE.match(cand) and (len(lines) >= 2 or not tail):
        body = [" ".join(seg) for seg in lines[:-1]] + tail
        name = " ".join(body).strip()
        return cand, (name or cand)
    return None, " ".join(" ".join(seg) for seg in lines).strip()


def _count_pack_lines(words: list) -> int:
    """How many "for N pcs" price lines a page has, from its word list (the text of a photo grid
    comes out in pieces, so look for `for`, a number, then a unit)."""
    n = 0
    texts = [str(w[4]).lower() for w in words]
    for i in range(len(texts) - 2):
        if texts[i] == "for" and texts[i + 1].isdigit() and texts[i + 2].rstrip(".") in _PACK_UNITS:
            n += 1
    return n


_PACK_UNITS = {"pc", "pcs", "piece", "pieces", "set", "sets", "no", "nos"}

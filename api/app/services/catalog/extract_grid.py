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

import re
from collections import defaultdict
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


@dataclass
class Cell:
    page_no: int
    position: int  # reading order within the page, 1-based
    supplier_code: str
    name_raw: str
    cost_price: Decimal
    pack_qty: int
    image: bytes
    image_ext: str
    image_w: int
    image_h: int


@dataclass
class ExtractResult:
    page_count: int = 0
    cells: list[Cell] = field(default_factory=list)
    skipped_no_price: int = 0  # photos with no price line (blank filler cells, logos)
    warnings: list[str] = field(default_factory=list)


class NotACatalog(Exception):
    """The PDF could not be opened, or no product cells were found."""


def extract(pdf: bytes) -> ExtractResult:
    try:
        doc = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as exc:  # corrupt / not a PDF
        raise NotACatalog(f"Could not open the PDF: {exc}") from exc

    out = ExtractResult(page_count=len(doc))
    seen_codes: dict[str, int] = defaultdict(int)

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_no = page_idx + 1
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
            x0, y0, x1, y1 = b["bbox"]
            segs = _cell_lines(words, photos, b, page_h)
            parsed = _parse_cell(segs)
            if parsed is None:
                out.skipped_no_price += 1
                continue
            price, pack, rest = parsed
            code, name_raw = _split_code(rest)
            if code is None:
                code = f"P{page_no}-{pos}"
                out.warnings.append(f"page {page_no} cell {pos}: no product code line; used {code}")
            seen_codes[code] += 1
            if seen_codes[code] > 1:
                dup = f"{code}~{seen_codes[code]}"
                out.warnings.append(
                    f"page {page_no} cell {pos}: code {code} repeated; kept as {dup}"
                )
                code = dup
            out.cells.append(
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

    if not out.cells:
        raise NotACatalog(
            "No products found. This PDF's layout isn't recognised: each product needs a "
            "photo with a price line (for example 'Rs 190 for 6 pcs') under it."
        )
    return out


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

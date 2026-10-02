"""Build small synthetic catalog PDFs for parser edge cases.

Layout mirrors the real supplier files: 3 columns, one photo per cell, then a
price line, the wrapped name, and the product code as the last line.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pymupdf
from PIL import Image

PHOTO_W, PHOTO_H = 180.0, 120.0
COL_X = (18.0, 216.0, 414.0)
ROW_Y = (20.0, 190.0, 360.0, 530.0)  # four rows per page


def photo(color: tuple[int, int, int] = (200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), color).save(buf, "JPEG")
    return buf.getvalue()


@dataclass
class TestCell:
    __test__ = False  # not a pytest class

    price_line: str | None = "Rs 190 for 6 pcs IN STOCK"
    name_lines: list[str] = field(
        default_factory=lambda: ["DELI 190 ML JUICE GLASS", "COL BOX 6 PC"]
    )
    code: str | None = "TKB512G"
    # put a stray word on the code's line, far to the right (a wrapped name word)
    code_line_tail: str | None = None
    image: bytes | None = None


def make_pdf(pages: list[list[TestCell]]) -> bytes:
    doc = pymupdf.open()
    default_photo = photo()
    for cells in pages:
        page = doc.new_page(width=612, height=792)
        for idx, cell in enumerate(cells):
            row, col = divmod(idx, 3)
            x, y = COL_X[col], ROW_Y[row]
            page.insert_image(
                pymupdf.Rect(x, y, x + PHOTO_W, y + PHOTO_H), stream=cell.image or default_photo
            )
            ty = y + PHOTO_H + 12
            if cell.price_line:
                page.insert_text((x + 4, ty), cell.price_line, fontsize=9)
                ty += 11
            for line in cell.name_lines:
                page.insert_text((x + 4, ty), line, fontsize=8)
                ty += 10
            if cell.code:
                page.insert_text((x + 4, ty), cell.code, fontsize=8)
                if cell.code_line_tail:
                    page.insert_text((x + 100, ty), cell.code_line_tail, fontsize=8)
    out = doc.tobytes()
    doc.close()
    return out


def text_only_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Quarterly report. No products here.", fontsize=12)
    out = doc.tobytes()
    doc.close()
    return out

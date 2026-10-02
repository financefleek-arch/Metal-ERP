"""Text helpers shared by the catalog PDFs (labels and the customer catalog).

The PDFs use the built-in Helvetica, which draws Latin-1 only and has no rupee sign:
names are folded to Latin-1 and prices read "Rs 1,256".
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal

import pymupdf

FONT = pymupdf.Font("helv")
FONT_BOLD = pymupdf.Font("hebo")


def latin1(text: str) -> str:
    """Helvetica draws Latin-1 only: fold accents, drop anything else."""
    folded = unicodedata.normalize("NFKD", text).encode("latin-1", "ignore").decode("latin-1")
    return " ".join(folded.split())


def inr(amount: Decimal) -> str:
    """'Rs 1,256' / 'Rs 12,56,000' / 'Rs 99.50' with Indian digit grouping."""
    whole, _, frac = f"{amount:.2f}".partition(".")
    head, tail = (whole[:-3], whole[-3:]) if len(whole) > 3 else ("", whole)
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join([*groups, tail]) if groups else tail
    return f"Rs {text}" if frac == "00" else f"Rs {text}.{frac}"


def wrap(
    text: str, size: float, width: float, max_lines: int, *, bold: bool = False
) -> list[str]:
    """Greedy word wrap to `width` pt, at most `max_lines`; the last line ends in '..' when cut.
    Measures in bold when the text will be drawn bold."""
    font = FONT_BOLD if bold else FONT
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.text_length(trial, size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.text_length(last + "..", size) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + ".."
    fixed: list[str] = []
    for ln in lines:  # a single word wider than the box: cut it
        while len(ln) > 1 and font.text_length(ln, size) > width:
            ln = ln[:-1]
        fixed.append(ln)
    return fixed


def put_text(
    page: pymupdf.Page,
    x: float,
    y: float,
    text: str,
    size: float,
    *,
    bold: bool = False,
    align: str = "l",
    width: float = 0.0,
    color: tuple[float, float, float] = (0, 0, 0),
) -> None:
    """Draw `text` with its baseline at `y`. `align` 'r'/'c' positions within `width`."""
    font = FONT_BOLD if bold else FONT
    tw = font.text_length(text, size)
    if align == "r":
        x = x + width - tw
    elif align == "c":
        x = x + (width - tw) / 2
    page.insert_text(
        (x, y), text, fontsize=size, fontname="hebo" if bold else "helv", color=color
    )

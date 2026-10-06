"""Guess whether a supplier PDF is a bill or a price list, from a quick look at its text.

A bill carries a GSTIN, an invoice number and totals. A price list is a grid of photos each with
a price line ("Rs 190 for 6 pcs"). The guess is only a starting point: the person always sees it
and can switch, and nothing is approved on the strength of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

# "for 6 pcs": the pack size printed in each price line of a price list. The text of a photo grid
# comes out scrambled ("Rs", the number and "for 6 pcs" in separate places), so each part is
# counted on its own.
_PACK_LINE = re.compile(r"for\s+\d+\s*(?:pcs?|pieces?|sets?|nos)", re.IGNORECASE)
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_BILL_WORDS = (
    ("tax invoice", 2),
    ("invoice no", 2),
    ("bill no", 2),
    ("grand total", 1),
    ("taxable value", 1),
    ("cgst", 1),
    ("igst", 1),
    ("irn", 1),
)
_MAX_PAGES = 6  # a quick look is enough


@dataclass
class Guess:
    kind: str  # bill | price_list | unknown
    reasons: list[str] = field(default_factory=list)
    pages: int = 0


def guess(data: bytes) -> Guess:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001 - not a readable PDF
        return Guess("unknown", ["The file could not be read as a PDF."])
    with doc:
        text = ""
        images = 0
        for i, page in enumerate(doc):
            if i >= _MAX_PAGES:
                break
            text += "\n" + page.get_text()
            images += len(page.get_images(full=True))
        pages = len(doc)
    low = text.lower()
    bill, lst = 0, 0
    reasons: list[str] = []
    if _GSTIN.search(text):
        bill += 3
        reasons.append("has a GSTIN")
    for word, weight in _BILL_WORDS:
        if word in low:
            bill += weight
    if bill > 3:
        reasons.append("has invoice wording and totals")
    prices = len(_PACK_LINE.findall(text)) + low.count("in stock")
    if prices >= 6:
        lst += 3 + min(prices // 10, 3)
        reasons.append(f"{prices} price lines (for 6 pcs, in stock)")
    if images >= 3:
        lst += 1
        reasons.append(f"{images} pictures")
    if bill == 0 and lst == 0:
        return Guess("unknown", ["Could not tell from the text."], pages)
    if lst > bill:
        return Guess("price_list", [r for r in reasons if "price" in r or "pictures" in r], pages)
    return Guess("bill", [r for r in reasons if "GSTIN" in r or "invoice" in r], pages)

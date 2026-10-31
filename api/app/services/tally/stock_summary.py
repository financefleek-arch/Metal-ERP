"""Parse Tally's Stock Summary report into stock-on-hand rows (the "refill" of items, plan 2026-10-31).

The report is a flat list, one `DSPACCNAME` + `DSPSTKINFO` pair per stock item: name, closing quantity
with its unit written into the quantity text ("19 Pcs"), closing rate and closing value. It has no GUID,
no stock group and no HSN, so rows can only be matched to our items by name (the masters pull is what
carries the GUID). Real export: 920 rows, Pcs / NOS / Doz / Kgs / Set / BOX / GROSS.

Tally writes closing value as a negative number for stock it holds (a debit); `value` here is the
positive worth of the stock. Rows that are not stock items (adjustment lines like "Rate Difference",
"SLAB DISCOUNT") are kept but flagged, so the caller can count and skip them rather than lose the
information that they were in the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from lxml import etree

from app.services.tally.voucher_xml import _BAD_CHARS, _dec, parse_quantity

# The adjustment lines shops keep as stock items so Tally can post differences. Seen in the real export.
_ADJUSTMENT_NAMES = frozenset(
    {"other", "rate difference", "slab discount", "round off", "discount", "freight"}
)
_ADJUSTMENT_PREFIX = re.compile(r"^(rate difference|slab discount)\b", re.IGNORECASE)


@dataclass
class StockRow:
    name: str
    qty: Decimal | None  # None = Tally shows no quantity for it (not a counted item)
    unit: str | None
    rate: Decimal | None  # closing-stock rate (cost basis), not a selling price
    value: Decimal | None  # positive worth of the stock on hand
    adjustment: bool = False

    @property
    def negative(self) -> bool:
        """Stock that is oversold in Tally: a quantity below zero. Shown, never silently clamped."""
        return self.qty is not None and self.qty < 0


def is_adjustment(name: str) -> bool:
    key = " ".join(name.lower().split())
    return key in _ADJUSTMENT_NAMES or bool(_ADJUSTMENT_PREFIX.match(key))


def _decode(raw: bytes) -> bytes:
    """Tally's UI export is UTF-16 with a byte-order mark; the Gateway answers in UTF-8."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16").encode("utf-8")
    return raw


def parse_stock_summary(raw: bytes) -> list[StockRow]:
    cleaned = _BAD_CHARS.sub(b"", _decode(raw))
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(cleaned, parser)
    if root is None:
        return []
    out: list[StockRow] = []
    name: str | None = None
    for el in root:
        if el.tag == "DSPACCNAME":
            name = (el.findtext("DSPDISPNAME") or "").strip()
        elif el.tag == "DSPSTKINFO" and name:
            closing = el.find("DSPSTKCL")
            qty, unit = parse_quantity(closing.findtext("DSPCLQTY") or "") if closing is not None else (None, None)
            rate = _dec(closing.findtext("DSPCLRATE")) if closing is not None else None
            amount = _dec(closing.findtext("DSPCLAMTA")) if closing is not None else None
            out.append(
                StockRow(
                    name=name,
                    qty=qty,
                    unit=unit,
                    rate=rate,
                    value=abs(amount) if amount is not None else None,
                    adjustment=is_adjustment(name),
                )
            )
            name = None
    return out

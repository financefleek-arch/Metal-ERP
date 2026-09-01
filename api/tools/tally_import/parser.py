"""Parse a Tally Prime masters export (XML) into plain dataclasses.

Handles the two shapes Tally produces:
  - Export -> Masters -> All Masters  (GROUP + LEDGER + others interleaved)
  - Display -> List of Accounts -> Ledgers, Alt+E  (LEDGER only, groups as
    <PARENT> strings)

Encoding: Tally defaults to UTF-16LE with a BOM, but UTF-8 exports exist.
lxml wants bytes + a declared encoding; we sniff the BOM and strip a few
illegal control-char entities Tally is known to emit (&#4; etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

# Tally sometimes writes raw control-char entities that are illegal in XML 1.0.
_BAD_ENTITY_RE = re.compile(rb"&#(?:x0?[0-8bcef]|x1[0-9a-f]|[0-8]|1[0-9]|2[0-9]|3[01]);", re.I)


@dataclass
class TallyLedger:
    name: str
    parent: str | None = None
    guid: str | None = None
    gstin: str | None = None
    pan: str | None = None
    state_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address_lines: list[str] = field(default_factory=list)
    pincode: str | None = None
    raw_xml: str | None = None


@dataclass
class TallyGroup:
    name: str
    parent: str | None = None


@dataclass
class TallyStockItem:
    name: str
    parent: str | None = None  # the stock group
    guid: str | None = None
    base_units: str | None = None
    hsn: str | None = None
    gst_rate: float | None = None
    standard_rate: float | None = None
    opening_balance: str | None = None  # kept only to detect "zero-history" dummies
    has_transactions: bool = False
    raw_xml: str | None = None


@dataclass
class TallyMasters:
    ledgers: list[TallyLedger]
    groups: list[TallyGroup]


@dataclass
class TallyStock:
    items: list[TallyStockItem]
    groups: list[TallyGroup]  # stock groups (may nest via <PARENT>)


def _decode(raw: bytes) -> bytes:
    """Return UTF-8 bytes regardless of the source encoding, entities cleaned."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw[3:].decode("utf-8")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-16", errors="replace")
    # Drop the XML declaration's encoding so lxml doesn't fight us.
    text = re.sub(r"<\?xml[^>]*\?>", "", text, count=1)
    data = text.encode("utf-8")
    return _BAD_ENTITY_RE.sub(b"", data)


def _t(el: etree._Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    v = child.text.strip()
    return v or None


def _first(el: etree._Element, *tags: str) -> str | None:
    for tag in tags:
        v = _t(el, tag)
        if v:
            return v
    return None


def _address_lines(led: etree._Element) -> list[str]:
    lines: list[str] = []
    for lst_tag in ("ADDRESS.LIST", "LEDMAILINGDETAILS.LIST"):
        lst = led.find(lst_tag)
        if lst is None:
            continue
        for addr in lst.findall("ADDRESS"):
            if addr.text and addr.text.strip():
                lines.append(addr.text.strip())
        if lines:
            break
    return lines


def parse_masters(raw: bytes) -> TallyMasters:
    data = _decode(raw)
    root = etree.fromstring(data)  # noqa: S320 - input is an uploaded Tally file, no DTD/entity expansion needed

    ledgers: list[TallyLedger] = []
    groups: list[TallyGroup] = []

    for grp in root.iter("GROUP"):
        name = grp.get("NAME") or _t(grp, "NAME")
        if not name:
            continue
        groups.append(TallyGroup(name=name.strip(), parent=_first(grp, "PARENT")))

    for led in root.iter("LEDGER"):
        name = led.get("NAME") or _t(led, "NAME")
        if not name:
            continue
        ledgers.append(
            TallyLedger(
                name=name.strip(),
                parent=_first(led, "PARENT"),
                guid=_first(led, "GUID", "MASTERID"),
                gstin=_first(led, "PARTYGSTIN", "GSTIN", "GSTREGISTRATIONNUMBER"),
                pan=_first(led, "INCOMETAXNUMBER", "PANNO", "PAN"),
                state_name=_first(led, "LEDSTATENAME", "STATENAME"),
                phone=_first(led, "LEDGERMOBILE", "LEDGERPHONE", "PHONENUMBER"),
                email=_first(led, "EMAIL", "LEDGEREMAIL"),
                address_lines=_address_lines(led),
                pincode=_first(led, "PINCODE", "LEDPINCODE", "LEDGERPINCODE"),
                raw_xml=etree.tostring(led, encoding="unicode"),
            )
        )

    return TallyMasters(ledgers=ledgers, groups=groups)


def _num(v: str | None) -> float | None:
    if not v:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
    return float(m.group(0)) if m else None


def _gst_rate(si: etree._Element) -> float | None:
    # <GSTDETAILS.LIST><GSTRATE>18</GSTRATE> ... or a nested RATEDETAILS
    for tag in ("GSTRATE", "RATE"):
        for node in si.iter(tag):
            r = _num(node.text)
            if r is not None:
                return r
    return None


def parse_stock_items(raw: bytes) -> TallyStock:
    data = _decode(raw)
    root = etree.fromstring(data)  # noqa: S320

    groups: list[TallyGroup] = []
    for grp in root.iter("STOCKGROUP"):
        name = grp.get("NAME") or _t(grp, "NAME")
        if name:
            groups.append(TallyGroup(name=name.strip(), parent=_first(grp, "PARENT")))

    items: list[TallyStockItem] = []
    for si in root.iter("STOCKITEM"):
        name = si.get("NAME") or _t(si, "NAME")
        if not name:
            continue
        # any batch/opening/transaction sub-node ⇒ "has history"
        has_txn = any(
            si.find(t) is not None
            for t in ("BATCHALLOCATIONS.LIST", "OPENINGBATCHALLOCATIONS.LIST")
        )
        items.append(
            TallyStockItem(
                name=name.strip(),
                parent=_first(si, "PARENT"),
                guid=_first(si, "GUID", "MASTERID"),
                base_units=_first(si, "BASEUNITS", "ADDITIONALUNITS"),
                hsn=_first(si, "HSNCODE", "HSNMASTERNAME"),
                gst_rate=_gst_rate(si),
                standard_rate=_num(
                    _first(si, "STANDARDPRICE", "OPENINGRATE", "STANDARDCOST")
                ),
                opening_balance=_first(si, "OPENINGBALANCE", "OPENINGVALUE"),
                has_transactions=has_txn,
                raw_xml=etree.tostring(si, encoding="unicode"),
            )
        )

    return TallyStock(items=items, groups=groups)


def is_zero_history_dummy(si: TallyStockItem) -> bool:
    """A Tally scratch/rounding entry: no unit, no HSN, no opening balance,
    no transactions. Skipped on import.
    """
    ob = _num(si.opening_balance) or 0.0
    return (
        not (si.base_units and si.base_units.strip())
        and not (si.hsn and si.hsn.strip())
        and ob == 0.0
        and not si.has_transactions
    )

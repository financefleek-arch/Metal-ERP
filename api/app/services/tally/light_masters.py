"""Read Tally's own ids, names and closing figures for ledgers and stock items (the refill's two light queries).

Two Collection exports, both verified against a live TallyPrime 7.1 (2026-10-31, fixtures
`tally_ledgers_dev_sample.xml` and `tally_stock_items_dev_sample.xml`):

  - Ledger:    Name, Guid, Parent (the group), ClosingBalance, OpeningBalance
  - StockItem: Name, Guid, Parent, BaseUnits, ClosingBalance (quantity with its unit, "12 Pcs"),
               ClosingValue, ClosingRate ("649.41/Pcs")

They carry the same Tally id (`GUID`) the masters pull stored on our links, so every row is matched to our
records by id, not by spelling. About 500 bytes a row, so a shop with thousands of items stays in the low
megabytes (the full masters export is 78 MB for the pilot). Requests are built in `voucher_capture.py`.

Signs, as Tally writes them: a ledger's closing balance is negative when the party owes the firm (a debit)
and positive when the firm owes them; stock value is negative for stock held (a debit). `ItemRow.value`
is the positive worth. Rows that are not parties or items (Cash, Profit & Loss A/c, expense ledgers) are
returned as they are; the caller decides which it cares about by matching ids and by walking the
group lineage with `party_tree` (the same `GroupTree` the masters import uses).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from lxml import etree

from app.services.tally.voucher_xml import _BAD_CHARS, _dec, parse_quantity, parse_rate
from tools.tally_import.groups import GroupTree
from tools.tally_import.parser import TallyGroup


@dataclass
class LedgerRow:
    guid: str
    name: str
    group: str  # the ledger's parent group as Tally names it
    closing: Decimal | None  # Tally's sign: negative = the party owes the firm
    opening: Decimal | None
    alter_id: int | None = None  # Tally's change counter for this record

    @property
    def owed_to_firm(self) -> Decimal:
        """What the party owes the firm: positive = they owe us, negative = we owe them (or an advance).
        The sign `party.opening_balance` uses."""
        return -(self.closing or Decimal(0))


@dataclass
class ItemRow:
    guid: str
    name: str
    group: str
    unit: str | None  # the item's base unit
    qty: Decimal | None  # None = Tally shows no closing quantity (nothing held)
    qty_unit: str | None
    rate: Decimal | None  # closing-stock cost, not a selling price
    value: Decimal | None  # positive worth of the stock held
    alter_id: int | None = None

    @property
    def negative(self) -> bool:
        return self.qty is not None and self.qty < 0


def _root(raw: bytes) -> etree._Element | None:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    return etree.fromstring(_BAD_CHARS.sub(b"", raw), parser)


def _name(el: etree._Element) -> str:
    return (el.get("NAME") or el.findtext("NAME") or "").strip()


def _clean(text: str | None) -> str:
    return (text or "").replace("\x04", "").strip()


def _alter(el: etree._Element) -> int | None:
    text = (el.findtext("ALTERID") or "").strip()
    return int(text) if text.isdigit() else None


def parse_groups(raw: bytes) -> list[TallyGroup]:
    """The group list (name and parent), so a ledger's lineage can be walked to Sundry Debtors or
    Creditors. A shop's customers usually sit under groups of its own (Positive: AASAM, SILIGURI, ...)."""
    root = _root(raw)
    out: list[TallyGroup] = []
    if root is None:
        return out
    for el in root.iter("GROUP"):
        name = _name(el)
        if name:
            out.append(TallyGroup(name=name, parent=_clean(el.findtext("PARENT")) or None))
    return out


def parse_voucher_types(raw: bytes) -> dict[str, str]:
    """Voucher type name -> its parent type (a built-in one is its own parent)."""
    root = _root(raw)
    out: dict[str, str] = {}
    if root is None:
        return out
    for el in root.iter("VOUCHERTYPE"):
        name = _name(el)
        parent = _clean(el.findtext("PARENT"))
        if name and parent:
            out[name] = parent
    return out


def party_tree(groups: list[TallyGroup] | None) -> GroupTree:
    """The lineage walker the masters import uses. With no groups it still knows the two root names."""
    return GroupTree(groups or [])


def parse_ledgers(raw: bytes) -> list[LedgerRow]:
    root = _root(raw)
    out: list[LedgerRow] = []
    if root is None:
        return out
    for el in root.iter("LEDGER"):
        name, guid = _name(el), (el.findtext("GUID") or "").strip()
        if not name or not guid:  # the <LEDGER>0</LEDGER> counter in the file's header has neither
            continue
        out.append(
            LedgerRow(
                guid=guid,
                name=name,
                group=_clean(el.findtext("PARENT")),
                closing=_dec(el.findtext("CLOSINGBALANCE")),
                opening=_dec(el.findtext("OPENINGBALANCE")),
                alter_id=_alter(el),
            )
        )
    return out


def parse_stock_items(raw: bytes) -> list[ItemRow]:
    root = _root(raw)
    out: list[ItemRow] = []
    if root is None:
        return out
    for el in root.iter("STOCKITEM"):
        name, guid = _name(el), (el.findtext("GUID") or "").strip()
        if not name or not guid:
            continue
        qty, qty_unit = parse_quantity(el.findtext("CLOSINGBALANCE") or "")
        rate, _ = parse_rate(el.findtext("CLOSINGRATE") or "")
        value = _dec(el.findtext("CLOSINGVALUE"))
        unit = _clean(el.findtext("BASEUNITS")) or None
        out.append(
            ItemRow(
                guid=guid,
                name=name,
                group=_clean(el.findtext("PARENT")),
                unit=unit,
                qty=qty,
                qty_unit=qty_unit or unit,
                rate=rate,
                value=abs(value) if value is not None else None,
                alter_id=_alter(el),
            )
        )
    return out

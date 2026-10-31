"""Parse a Tally voucher export into plain records (plan P1, docs/PLAN-tally-voucher-import-and-party-prices.md).

Input is the XML the `pull_vouchers` job parked in R2 (request built in `voucher_capture.py`). Output is
`ParsedVoucher` records only: nothing here touches the database, resolves a party or decides what a voucher
becomes. That is `voucher_import.py`.

Tally's sign convention, kept as it comes: in a ledger line a debit is negative and a credit positive
(a sales voucher carries the customer at -960.00, the sales ledger at +960.00; a receipt carries the
customer at +amount and cash or bank at -amount). `ParsedLedgerLine.amount` is that signed value;
`ParsedVoucher.party_amount` is the unsigned size of the party's line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree

# XML 1.0 forbids most control characters, but Tally emits &#4; for "Not Applicable". Drop them before
# parsing rather than let one batch name kill a month of vouchers.
_BAD_CHARS = re.compile(rb"&#(?:[0-8]|1[1-2]|1[4-9]|2[0-9]|3[01]);|[\x00-\x08\x0b\x0c\x0e-\x1f]")

SALES, RECEIPT, CREDIT_NOTE, JOURNAL, DEBIT_NOTE, PURCHASE, PAYMENT, OTHER = (
    "sales",
    "receipt",
    "credit_note",
    "journal",
    "debit_note",
    "purchase",
    "payment",
    "other",
)
_PARENTS = {
    "sales": SALES,
    "receipt": RECEIPT,
    "credit note": CREDIT_NOTE,
    "journal": JOURNAL,
    "debit note": DEBIT_NOTE,
    "purchase": PURCHASE,
    "payment": PAYMENT,
}


@dataclass
class ParsedBill:
    name: str
    bill_type: str  # "New Ref" | "Agst Ref" | "Advance" | "On Account" (as Tally spells it)
    amount: Decimal
    credit_days: int | None = None


@dataclass
class ParsedLedgerLine:
    ledger: str
    amount: Decimal
    is_party: bool
    bills: list[ParsedBill] = field(default_factory=list)


@dataclass
class ParsedItemLine:
    item: str
    qty: Decimal | None
    unit: str | None
    rate: Decimal | None
    rate_unit: str | None
    amount: Decimal
    discount: Decimal | None = None


@dataclass
class ParsedVoucher:
    guid: str
    master_id: int | None
    alter_id: int | None
    type_name: str  # as the shop named it: "Sales", "Sales - Cash", "Export Sales"
    kind: str  # parent family, see classify()
    number: str
    day: date | None
    party: str
    reference: str
    narration: str
    cancelled: bool
    optional: bool
    deleted: bool
    ledgers: list[ParsedLedgerLine] = field(default_factory=list)
    items: list[ParsedItemLine] = field(default_factory=list)

    @property
    def party_amount(self) -> Decimal:
        """Size of the party's own line, unsigned; zero when the voucher has no party line."""
        for line in self.ledgers:
            if line.is_party:
                return abs(line.amount)
        if self.party:
            for line in self.ledgers:
                if line.ledger == self.party:
                    return abs(line.amount)
        return Decimal(0)


def classify(type_name: str, parents: dict[str, str] | None = None) -> str:
    """Which family a voucher type belongs to. A shop's custom types ("Sales - Cash") report their
    parent when the agent sends a type->parent map; without one the name decides, and anything
    unrecognised is OTHER (counted, never applied)."""
    key = (type_name or "").strip().lower()
    if parents:
        parent = parents.get(type_name) or parents.get(key)
        if parent:
            # Tally says what this type is: trust it exactly. A "Sales Order" or "Delivery Note" is not a
            # sale, and a shop's "Estimate" is whatever its parent says, whatever the name.
            return _PARENTS.get(parent.strip().lower(), OTHER)
    if key in _PARENTS:
        return _PARENTS[key]
    if any(word in key for word in ("order", "delivery", "quotation", "estimate", "challan", "memo")):
        return OTHER  # without the parent to go by, a document that is not an invoice is not guessed to be one
    # most specific first: "credit note" before anything containing "note"
    for needle, family in (
        ("credit note", CREDIT_NOTE),
        ("debit note", DEBIT_NOTE),
        ("sales return", CREDIT_NOTE),
        ("purchase return", DEBIT_NOTE),
        ("receipt", RECEIPT),
        ("payment", PAYMENT),
        ("journal", JOURNAL),
        ("purchase", PURCHASE),
        ("sales", SALES),
        ("sale", SALES),
    ):
        if needle in key:
            return family
    return OTHER


def _text(el: etree._Element | None, tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _yes(value: str) -> bool:
    return value.strip().lower() == "yes"


def _int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.lstrip("-").isdigit() else None


_QTY = re.compile(r"^\s*(-?[\d,]*\.?\d+)\s*(.*?)\s*$")


def parse_quantity(text: str) -> tuple[Decimal | None, str | None]:
    """' 10 Nos' -> (10, 'Nos'). Compound quantities ('10 Nos = 2 Box') use the first figure."""
    if not text:
        return None, None
    first = text.split("=")[0]
    m = _QTY.match(first)
    if not m:
        return None, None
    return _dec(m.group(1)), (m.group(2) or None)


def parse_rate(text: str) -> tuple[Decimal | None, str | None]:
    """'96.00/Nos' -> (96.00, 'Nos'). Unit may be absent."""
    if not text:
        return None, None
    num, _, unit = text.partition("/")
    return _dec(num), (unit.strip() or None)


def parse_day(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%Y%m%d", "%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _bills(parent: etree._Element) -> list[ParsedBill]:
    out: list[ParsedBill] = []
    for b in parent.findall("BILLALLOCATIONS.LIST"):
        amount = _dec(_text(b, "AMOUNT"))
        bill_type = _text(b, "BILLTYPE")
        if amount is None or not bill_type:
            continue
        out.append(
            ParsedBill(
                name=_text(b, "NAME"),
                bill_type=bill_type,
                amount=amount,
                credit_days=_int(_text(b, "BILLCREDITPERIOD")),
            )
        )
    return out


def _ledgers(v: etree._Element) -> list[ParsedLedgerLine]:
    # ALLLEDGERENTRIES is the full list; LEDGERENTRIES repeats a subset of it in the same voucher, so
    # reading both would double the party line. Fall back to it only when the full list is absent.
    nodes = v.findall("ALLLEDGERENTRIES.LIST") or v.findall("LEDGERENTRIES.LIST")
    out: list[ParsedLedgerLine] = []
    for n in nodes:
        amount = _dec(_text(n, "AMOUNT"))
        name = _text(n, "LEDGERNAME")
        if amount is None or not name:
            continue
        out.append(
            ParsedLedgerLine(
                ledger=name,
                amount=amount,
                is_party=_yes(_text(n, "ISPARTYLEDGER")),
                bills=_bills(n),
            )
        )
    return out


def _items(v: etree._Element) -> list[ParsedItemLine]:
    out: list[ParsedItemLine] = []
    for n in v.findall("ALLINVENTORYENTRIES.LIST") + v.findall("INVENTORYENTRIES.LIST"):
        name = _text(n, "STOCKITEMNAME")
        amount = _dec(_text(n, "AMOUNT"))
        if not name or amount is None:
            continue
        qty, unit = parse_quantity(_text(n, "BILLEDQTY") or _text(n, "ACTUALQTY"))
        rate, rate_unit = parse_rate(_text(n, "RATE"))
        out.append(
            ParsedItemLine(
                item=name,
                qty=qty,
                unit=unit,
                rate=rate,
                rate_unit=rate_unit or unit,
                amount=amount,
                discount=_dec(_text(n, "DISCOUNT")),
            )
        )
    return out


def voucher_from_element(
    v: etree._Element, parents: dict[str, str] | None = None, *, allow_no_guid: bool = False
) -> ParsedVoucher | None:
    """One `<VOUCHER>` as a record. A voucher with no GUID is skipped, unless `allow_no_guid` (a Tally
    file export may carry none: it then gets a key from its type, number and date)."""
    if len(v) == 0:
        return None  # the "<VOUCHER>0</VOUCHER>" counter in an export's header is not a voucher
    guid = _text(v, "GUID") or (v.get("REMOTEID") or "").strip()
    type_name = _text(v, "VOUCHERTYPENAME") or (v.get("VCHTYPE") or "")
    number = _text(v, "VOUCHERNUMBER")
    if not guid:
        if not allow_no_guid:
            return None
        guid = f"{type_name}|{number}|{_text(v, 'DATE')}"
    return ParsedVoucher(
        guid=guid,
        master_id=_int(_text(v, "MASTERID")),
        alter_id=_int(_text(v, "ALTERID")),
        type_name=type_name,
        kind=classify(type_name, parents),
        number=number,
        day=parse_day(_text(v, "DATE")),
        party=_text(v, "PARTYLEDGERNAME"),
        reference=_text(v, "REFERENCE"),
        narration=_text(v, "NARRATION"),
        cancelled=_yes(_text(v, "ISCANCELLED")),
        optional=_yes(_text(v, "ISOPTIONAL")),
        deleted=_yes(_text(v, "ISDELETED")),
        ledgers=_ledgers(v),
        items=_items(v),
    )


def parse_vouchers(raw: bytes, parents: dict[str, str] | None = None) -> list[ParsedVoucher]:
    """All vouchers in one export, in file order. A voucher that cannot be read (no GUID) is skipped;
    the caller counts the difference between what it expected and what came back."""
    cleaned = _BAD_CHARS.sub(b"", raw)
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(cleaned, parser)
    if root is None:
        return []
    out: list[ParsedVoucher] = []
    for v in root.iter("VOUCHER"):
        parsed = voucher_from_element(v, parents)
        if parsed is not None:
            out.append(parsed)
    return out


class _CleanReader:
    """A file-like over a Tally export for the XML parser: UTF-16 (what Tally's own Export writes) becomes
    UTF-8, and the control characters Tally emits ("&#4;" for "Not Applicable") are dropped, so a file of
    any size can be read in one pass without holding it in memory."""

    _KEEP = 16  # bytes held back so a "&#4;" split across two reads is still seen whole

    def __init__(self, stream) -> None:  # type: ignore[no-untyped-def]
        self._stream = stream
        head = stream.read(4)
        self._decoder = None
        self._pending = b""
        if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
            import codecs

            self._decoder = codecs.getincrementaldecoder("utf-16")()
        self._raw_first = head
        self._eof = False

    def _pull(self, size: int) -> bytes:
        if self._raw_first:
            chunk, self._raw_first = self._raw_first, b""
            more = self._stream.read(max(size - len(chunk), 0))
            chunk += more
        else:
            chunk = self._stream.read(size)
        if not chunk:
            self._eof = True
        if self._decoder is not None:
            return self._decoder.decode(chunk, final=self._eof).encode("utf-8")
        return chunk

    def read(self, size: int = 65536) -> bytes:
        while len(self._pending) < size + self._KEEP and not self._eof:
            self._pending += self._pull(size)
        if self._eof:
            data, self._pending = self._pending, b""
        else:
            data, self._pending = self._pending[: -self._KEEP], self._pending[-self._KEEP :]
        return _BAD_CHARS.sub(b"", data)


def iter_vouchers(stream, parents: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    """The vouchers of a Tally file export, one at a time, so a file of hundreds of MB never sits in memory.
    A UI export (Day Book, Export > XML) and the Gateway's answer are both read; a voucher with no GUID gets
    a key made from its type, number and date."""
    reader = _CleanReader(stream)
    try:
        for _event, el in etree.iterparse(
            reader, events=("end",), tag="VOUCHER", recover=True, huge_tree=True, resolve_entities=False
        ):
            parsed = voucher_from_element(el, parents, allow_no_guid=True)
            if parsed is not None:
                yield parsed
            el.clear()
            while el.getprevious() is not None:
                parent = el.getparent()
                if parent is None:
                    break
                del parent[0]
    except etree.XMLSyntaxError:
        return  # an empty or cut-off file: what was read before it stays read


@dataclass
class PendingBill:
    ref: str
    party: str
    day: date | None
    due: date | None
    closing: Decimal  # Tally's sign: negative = the party owes us


def parse_bills_receivable(raw: bytes) -> list[PendingBill]:
    """The `Bills Receivable` export: BILLFIXED / BILLCL / BILLDUE repeat in sequence under one root."""
    cleaned = _BAD_CHARS.sub(b"", raw)
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(cleaned, parser)
    if root is None:
        return []
    out: list[PendingBill] = []
    current: dict | None = None
    for el in root:
        if el.tag == "BILLFIXED":
            current = {
                "ref": _text(el, "BILLREF"),
                "party": _text(el, "BILLPARTY"),
                "day": parse_day(_text(el, "BILLDATE")),
                "closing": Decimal(0),
                "due": None,
            }
            out.append(PendingBill(**current))
        elif current is not None and out:
            if el.tag == "BILLCL":
                out[-1].closing = _dec(el.text) or Decimal(0)
            elif el.tag == "BILLDUE":
                out[-1].due = parse_day(el.text or "")
    return out

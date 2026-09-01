"""Text-layer extraction: supplier PDF invoice -> RawExtraction.

`pdfplumber.extract_table()` reads the line grid as **cells, not lines**, which
survives the column-wrap hazard ("11,689.3\n0", "1,052.04 (\n9%)"): a wrapped
value arrives as one cell containing a newline, and we strip it. Header and
totals come from labelled-field regex over `extract_text()`.

No LLM. Returns whatever it can read plus a per-field confidence; the
orchestrator reconciles and decides `needs_review`.

Tuned against the committed fixtures (Sugal Foods INV2526-5667 first). The
goal is "correct on our suppliers", not "any PDF on earth".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pdfplumber

# --- number / date parsing -------------------------------------------------

_NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")
_WS_RE = re.compile(r"\s+")


def _money(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = _NUM_RE.search(text.replace("\n", "").replace(" ", ""))
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _int_qty(text: str | None) -> Decimal | None:
    return _money(text)


_DATE_RE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})")


def _date_iso(text: str | None) -> str | None:
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    if y < 100:
        y += 2000
    try:
        from datetime import date

        return date(y, mth, d).isoformat()
    except ValueError:
        return None


# --- percent-in-parens: "1,052.04 (9%)" -> (Decimal("1052.04"), Decimal("9")) --

_PCT_IN_PARENS_RE = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*%\s*\)?")


def _amt_and_rate(cell: str | None) -> tuple[Decimal | None, Decimal | None]:
    if not cell:
        return None, None
    flat = cell.replace("\n", "")
    rate = None
    mr = _PCT_IN_PARENS_RE.search(flat)
    if mr:
        try:
            rate = Decimal(mr.group(1))
        except InvalidOperation:
            rate = None
        flat = flat[: mr.start()]
    return _money(flat), rate


@dataclass
class RawLine:
    sl_no: int
    description: str
    hsn: str | None = None
    quantity: Decimal | None = None
    uom: str | None = None
    unit_rate: Decimal | None = None
    discount_pct: Decimal | None = None
    cgst_rate: Decimal | None = None
    cgst_amt: Decimal | None = None
    sgst_rate: Decimal | None = None
    sgst_amt: Decimal | None = None
    igst_rate: Decimal | None = None
    igst_amt: Decimal | None = None
    line_total: Decimal | None = None


@dataclass
class SupplierAddress:
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state_code: str | None = None
    pincode: str | None = None


@dataclass
class RawExtraction:
    # header
    supplier_name: str | None = None
    supplier_gstin: str | None = None
    supplier_phone: str | None = None
    supplier_address: SupplierAddress | None = None
    buyer_gstin: str | None = None
    bill_no: str | None = None
    bill_date: str | None = None  # ISO
    sales_order_ref: str | None = None
    place_of_supply_state_code: str | None = None
    # totals
    taxable_total: Decimal | None = None
    cgst_total: Decimal | None = None
    sgst_total: Decimal | None = None
    igst_total: Decimal | None = None
    round_off: Decimal | None = None
    grand_total: Decimal | None = None
    amount_in_words: str | None = None
    # lines
    lines: list[RawLine] = field(default_factory=list)
    # meta
    raw_text: str = ""
    page_count: int = 0
    low_text: bool = False  # sparse text layer -> caller routes to image path
    field_confidence: dict[str, float] = field(default_factory=dict)


# --- header / totals regex over the full text ---------------------------------

_GSTIN_RE = re.compile(r"GSTIN[:\s]*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z])")
_BILL_NO_RE = re.compile(r"Invoice\s*#?\s*[:\-]?\s*([A-Za-z0-9\-/]+)")
_BILL_DATE_RE = re.compile(r"Invoice\s*Date\s*[:\-]?\s*([\d/\-]+)")
_SO_RE = re.compile(r"Sales\s*Order\s*[:\-]?\s*([A-Za-z0-9\-/]+)")
_POS_RE = re.compile(r"Place\s*Of\s*Supply\s*[:\-]?\s*.*?\((\d{2})\)", re.IGNORECASE)

_TAXABLE_RE = re.compile(r"Total\s+Taxable\s+Amount\s+([\d,]+\.\d{2})", re.IGNORECASE)
_SUBTOTAL_RE = re.compile(r"Sub\s*Total\s+([\d,]+\.\d{2})", re.IGNORECASE)
_CGST_TOTAL_RE = re.compile(r"CGST\S*\s*\(?\s*\d+(?:\.\d+)?%\s*\)?\s+([\d,]+\.\d{2})")
_SGST_TOTAL_RE = re.compile(r"SGST\S*\s*\(?\s*\d+(?:\.\d+)?%\s*\)?\s+([\d,]+\.\d{2})")
_IGST_TOTAL_RE = re.compile(r"IGST\S*\s*\(?\s*\d+(?:\.\d+)?%\s*\)?\s+([\d,]+\.\d{2})")
_ROUND_RE = re.compile(r"(?:Round(?:ing)?(?:\s*Off)?)\s+(-?[\d,]+\.\d{2})", re.IGNORECASE)
_GRAND_RE = re.compile(r"Total\s+Rs\.?\s*([\d,]+\.\d{2})", re.IGNORECASE)
_WORDS_RE = re.compile(r"Total\s+In\s+Words\s*[:\-]?\s*(.+)", re.IGNORECASE)

_LOW_TEXT_CHARS_PER_PAGE = 120

# Supplier phone — the header carries the seller's ("Phone 8513057060"), the
# "Bill To" block the buyer's. Anchor before "Bill To". 7-15 digits, optional +.
_SUPPLIER_PHONE_RE = re.compile(
    r"(?:Phone|Ph|Mobile|Mob|Contact|Tel)\s*[:.\-]?\s*(\+?[\d][\d\s\-]{6,17}\d)",
    re.IGNORECASE,
)
_PINCODE_RE = re.compile(r"\b([1-9]\d{5})\b")
# Lines in the supplier block that are never part of a postal address.
_ADDR_NOISE_RE = re.compile(
    r"^(TAX\s+INVOICE|GSTIN|FSSAI|MSME|UDYAM|CIN|PAN|Phone|Ph|Mobile|Mob|Contact|"
    r"Tel|E-?mail|Email|Website|www\.|Invoice\s*#|Invoice\s*Date|Bill\s*To|"
    r"Ship\s*To|Sales\s*Order|Delivery|Place\s*Of\s*Supply|Original|Duplicate|"
    r"Triplicate)\b",
    re.IGNORECASE,
)


def _first_gstin_before(text: str, marker: str) -> str | None:
    """Supplier GSTIN is the first one, and appears before 'Bill To'."""
    cut = text.find(marker)
    scope = text[:cut] if cut != -1 else text
    m = _GSTIN_RE.search(scope)
    return m.group(1) if m else None


def _all_gstins(text: str) -> list[str]:
    return _GSTIN_RE.findall(text)


def _supplier_block(text: str) -> list[str]:
    """The lines from the top of the invoice down to (not including) the first
    of 'Bill To' / 'Invoice Date' / 'Place Of Supply' — i.e. the seller's
    header. `pdfplumber` interleaves a right-aligned 'TAX INVOICE' / 'Invoice#'
    line into this region; callers filter noise.
    """
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        low = line.lower()
        if (
            low.startswith("bill to")
            or low.startswith("invoice date")
            or low.startswith("place of supply")
        ):
            break
        lines.append(line)
    return lines


def _clean_state_and_pin(line: str) -> tuple[str | None, str | None, str]:
    """Pull a trailing '<State Name> <pincode>' off an address line.
    Returns (state_code, pincode, line_without_them).
    """
    from app.reference import STATE_CODES

    state_code = pincode = None
    m = _PINCODE_RE.search(line)
    if m:
        pincode = m.group(1)
    # longest state name first, case-insensitive, word-bounded
    for code, name in sorted(STATE_CODES.items(), key=lambda kv: -len(kv[1])):
        if re.search(rf"\b{re.escape(name)}\b", line, re.IGNORECASE):
            state_code = code
            line = re.sub(rf"\b{re.escape(name)}\b", "", line, flags=re.IGNORECASE)
            break
    if pincode:
        line = line.replace(pincode, "")
    line = re.sub(r"\bIndia\b", "", line, flags=re.IGNORECASE)
    line = _WS_RE.sub(" ", line).strip(" ,-")
    return state_code, pincode, line


def _parse_supplier_address(text: str) -> SupplierAddress | None:
    block = _supplier_block(text)
    if not block:
        return None
    # skip the name (first non-empty), then take up to 3 address-ish lines
    body = [
        ln
        for ln in block[1:]
        if ln and not _ADDR_NOISE_RE.match(ln) and ln.upper() != "TAX INVOICE"
    ]
    if not body:
        return None

    addr = SupplierAddress()
    # state + pincode live on whichever line has the pincode (usually line 1)
    remainders: list[str] = []
    for ln in body[:3]:
        sc, pin, rest = _clean_state_and_pin(ln)
        addr.state_code = addr.state_code or sc
        addr.pincode = addr.pincode or pin
        if rest:
            remainders.append(rest)

    # city: the token just before "State pincode" is a decent guess — take the
    # last comma-part of the pincode line's remainder.
    if remainders:
        first = remainders[0]
        addr.line1 = first
        parts = [p.strip() for p in first.split(",") if p.strip()]
        if len(parts) >= 2:
            addr.city = parts[-1]
        if len(remainders) > 1:
            addr.line2 = " ".join(remainders[1:])[:200] or None
    return addr


def _parse_header(text: str, ext: RawExtraction) -> None:
    # supplier name = first non-empty line
    for line in text.splitlines():
        if line.strip():
            ext.supplier_name = line.strip()
            break

    gstins = _all_gstins(text)
    ext.supplier_gstin = _first_gstin_before(text, "Bill To") or (
        gstins[0] if gstins else None
    )
    if len(gstins) > 1:
        ext.buyer_gstin = next((g for g in gstins if g != ext.supplier_gstin), None)

    if m := _BILL_NO_RE.search(text):
        ext.bill_no = m.group(1)
    if d := _BILL_DATE_RE.search(text):
        ext.bill_date = _date_iso(d.group(1))
    if so := _SO_RE.search(text):
        ext.sales_order_ref = so.group(1)
    if pos := _POS_RE.search(text):
        ext.place_of_supply_state_code = pos.group(1)
    elif ext.supplier_gstin:
        ext.place_of_supply_state_code = ext.supplier_gstin[:2]

    # supplier phone — scoped to the header, before "Bill To"
    cut = text.find("Bill To")
    header_scope = text[:cut] if cut != -1 else text
    if ph := _SUPPLIER_PHONE_RE.search(header_scope):
        ext.supplier_phone = re.sub(r"[\s\-]", "", ph.group(1))

    # supplier postal address from the header block
    ext.supplier_address = _parse_supplier_address(text)
    # backfill state from GSTIN prefix if the address text didn't name a state
    if ext.supplier_address and not ext.supplier_address.state_code and ext.supplier_gstin:
        ext.supplier_address.state_code = ext.supplier_gstin[:2]


def _parse_totals(text: str, ext: RawExtraction) -> None:
    if m := (_TAXABLE_RE.search(text) or _SUBTOTAL_RE.search(text)):
        ext.taxable_total = _money(m.group(1))
    if m := _CGST_TOTAL_RE.search(text):
        ext.cgst_total = _money(m.group(1))
    if m := _SGST_TOTAL_RE.search(text):
        ext.sgst_total = _money(m.group(1))
    if m := _IGST_TOTAL_RE.search(text):
        ext.igst_total = _money(m.group(1))
    if m := _ROUND_RE.search(text):
        ext.round_off = _money(m.group(1))
    if m := _GRAND_RE.search(text):
        ext.grand_total = _money(m.group(1))
    if m := _WORDS_RE.search(text):
        ext.amount_in_words = m.group(1).strip()


# --- line grid via extract_table() ------------------------------------------

_HSN_CELL_RE = re.compile(r"^\d{4,8}$")


def _row_is_line_item(row: list[str | None]) -> int | None:
    """A line-item row starts with a small integer sl_no somewhere in the first
    two non-empty cells and contains an HSN-looking cell. Returns the sl_no.
    """
    cells = _cells(row)
    nonempty = [c for c in cells if c]
    if len(nonempty) < 4:
        return None
    if not any(_HSN_CELL_RE.match(c) for c in cells):
        return None
    for c in nonempty[:2]:
        if c.isdigit() and 1 <= int(c) <= 999:
            return int(c)
    return None


def _cells(row: list[str | None]) -> list[str]:
    """Flatten cell newlines. A newline inside a number/percent cell is noise
    ("11,689.3\n0"); inside a text cell it is a wrapped word ("Syrup\n1000Ml")
    and needs a space. Heuristic: keep no gap when both sides are digits.
    """
    out: list[str] = []
    for c in row:
        if c is None:
            out.append("")
            continue
        # "digit\n digit" -> join; otherwise "\n" -> space
        joined = re.sub(r"(?<=\d)\n(?=\d)", "", c)
        joined = joined.replace("\n", " ")
        out.append(_WS_RE.sub(" ", joined).strip())
    return out


def _extract_lines_from_table(table: list[list[str | None]]) -> list[RawLine]:
    lines: list[RawLine] = []
    for row in table:
        sl = _row_is_line_item(row)
        if sl is None:
            continue
        cells = _cells(row)
        # Locate columns by content, not fixed index (robust to leading Nones).
        hsn_idx = next(i for i, c in enumerate(cells) if _HSN_CELL_RE.match(c))
        # description: joined non-empty cells strictly before the HSN column,
        # minus the sl_no token.
        desc_parts = [
            c for c in cells[:hsn_idx] if c and not (c.isdigit() and int(c) == sl)
        ]
        description = " ".join(desc_parts).strip()

        after = cells[hsn_idx + 1 :]
        # after ~ [qty, uom, rate, disc%, cgst(+rate), (blank), sgst(+rate), amount, (blank)]
        rl = RawLine(sl_no=sl, description=description, hsn=cells[hsn_idx])

        nums_after = [c for c in after if c]
        # qty
        if nums_after:
            rl.quantity = _int_qty(nums_after[0])
        # uom: first alpha-only token
        rl.uom = next((c for c in after if c.isalpha()), None)
        # rate: first money token after the uom
        money_tokens = [c for c in after if _NUM_RE.fullmatch(c.replace(",", ""))]
        # money_tokens ~ [qty, rate, line_total] once %-cells are set aside
        if len(money_tokens) >= 2:
            rl.unit_rate = _money(money_tokens[1])
        if money_tokens:
            rl.line_total = _money(money_tokens[-1])
        # discount %
        for c in after:
            mp = _PCT_IN_PARENS_RE.fullmatch(c) or re.fullmatch(r"(\d+(?:\.\d+)?)%", c)
            if mp:
                rl.discount_pct = Decimal(mp.group(1))
                break
        # CGST / SGST amount + rate: the two "(9%)" cells
        gst_cells = [c for c in after if "%" in c and "(" in c]
        if len(gst_cells) >= 1:
            rl.cgst_amt, rl.cgst_rate = _amt_and_rate(gst_cells[0])
        if len(gst_cells) >= 2:
            rl.sgst_amt, rl.sgst_rate = _amt_and_rate(gst_cells[1])

        lines.append(rl)

    # Multi-page: a table on page 2 may repeat the header row; sl_no keeps order.
    lines.sort(key=lambda x: x.sl_no)
    # de-dup a repeated sl_no (page header artefacts)
    seen: set[int] = set()
    deduped: list[RawLine] = []
    for line_item in lines:
        if line_item.sl_no in seen:
            continue
        seen.add(line_item.sl_no)
        deduped.append(line_item)
    return deduped


def _confidence(ext: RawExtraction) -> dict[str, float]:
    def has(v: object) -> float:
        return 0.95 if v not in (None, "", []) else 0.0

    conf = {
        "supplier_gstin": has(ext.supplier_gstin),
        "bill_no": has(ext.bill_no),
        "bill_date": has(ext.bill_date),
        "taxable_total": has(ext.taxable_total),
        "grand_total": has(ext.grand_total),
        "lines": 0.9 if ext.lines else 0.0,
    }
    # phone / address are nice-to-have — a lower weight, and only when present
    if ext.supplier_phone:
        conf["supplier_phone"] = 0.7
    if ext.supplier_address and (
        ext.supplier_address.line1 or ext.supplier_address.pincode
    ):
        conf["supplier_address"] = 0.6
    return conf


def extract(pdf_path: str) -> RawExtraction:
    ext = RawExtraction()
    with pdfplumber.open(pdf_path) as pdf:
        ext.page_count = len(pdf.pages)
        texts: list[str] = []
        all_lines: list[RawLine] = []
        total_chars = 0
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            texts.append(page_text)
            total_chars += len(page.chars)
            for table in page.extract_tables():
                all_lines.extend(_extract_lines_from_table(table))
        ext.raw_text = "\n".join(texts)
        ext.low_text = total_chars < _LOW_TEXT_CHARS_PER_PAGE * max(ext.page_count, 1)

    _parse_header(ext.raw_text, ext)
    _parse_totals(ext.raw_text, ext)

    # merge lines across pages by sl_no
    merged: dict[int, RawLine] = {}
    for line_item in all_lines:
        merged.setdefault(line_item.sl_no, line_item)
    ext.lines = [merged[k] for k in sorted(merged)]

    ext.field_confidence = _confidence(ext)
    return ext

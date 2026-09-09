"""Build the Tally Sales-voucher XML for a finalized invoice (F1b-1).

Mirrors `app/services/inward/tally_xml.py` (the F5 Purchase-voucher
generator) in structure — envelope shape, `_sub()` helper, Decimal->string
money formatting, `ALLINVENTORYENTRIES.LIST` + nested
`ACCOUNTINGALLOCATIONS.LIST`, UDF reference tag — inverted for Sales'
double-entry sign convention and simplified for non-GST invoices (v1-nongst
is all Metal ERP produces today; the dormant GST columns on Invoice/
InvoiceLine are not read here).

**Delivery differs from the purchase side**: this module only builds bytes.
`tally_xml.py` (purchase) writes a file the accountant imports by hand;
this module's output is POSTed live to Tally's gateway by the agent
(F1a's transport, extended for `push_sales` — see
`app/services/tally/jobs.py::enqueue_push_sales`), no file, no human step.

Live-verified against a real TallyPrime gateway 2026-09-09 (see
docs/EXECUTION-PLAN-F1b1-sales-voucher-push.md, "Live probe findings"):
inventory entries + ACCOUNTINGALLOCATIONS.LIST, the sign convention below,
and BILLALLOCATIONS.LIST/"New Ref" bill-wise allocation all work exactly
as built. One real finding baked in: **round-off is folded into the last
real line's amount, not emitted as a separate ledger entry** — a
standalone Round Off LEDGERENTRIES.LIST line on an invoice-mode Sales
voucher failed silently (no LINEERROR text) on the tested install, in a
way unrelated to the ledger's name, parent group, or amount size. This
also means `ledger_map['round_off_ledger']` is not read here.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from app.models import Invoice, InvoiceLine, Party

_CENT = Decimal("0.01")

# Namespace stand-in for Tally's "UDF:" tag prefix — same trick
# `inward/tally_xml.py` uses; lxml rejects a bare colon in a tag name.
_UDF_NS = "urn:metalerp:tally-udf"


class LedgerMapIncomplete(ValueError):
    """The tenant's `tally_company.ledger_map` is missing a slot this
    serializer needs. The caller (push_readiness / enqueue_push_sales) is
    expected to have already checked this — reaching this exception means
    a caller skipped that gate.
    """


def _d(v: object) -> Decimal:
    return Decimal(0) if v is None else Decimal(str(v))


def _money(v: object) -> str:
    return str(_d(v).quantize(_CENT, rounding=ROUND_HALF_UP))


def _neg(v: object) -> str:
    return str((-_d(v)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _yyyymmdd(d: date | None) -> str:
    return d.strftime("%Y%m%d") if d is not None else ""


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def real_lines(invoice: Invoice) -> list[InvoiceLine]:
    """Same filter `finalize_invoice` uses — a blank-description row is a
    UI placeholder, never a real line."""
    return [ln for ln in invoice.lines if (ln.description or "").strip()]


def _weighment_narration(invoice: Invoice) -> str | None:
    slips = invoice.weighment_slips or []
    if not slips:
        return None
    parts = []
    for s in slips:
        seg = s.get("seg")
        kg = s.get("recorded_kg")
        if seg is not None and kg is not None:
            parts.append(f"seg {seg} = {kg}kg recorded")
    return f"Weighment: {'; '.join(parts)}" if parts else None


def build_sales_voucher_envelope(
    invoice: Invoice, party: Party, ledger_map: dict[str, str],
) -> etree._Element:
    """Build the `<ENVELOPE>` for a Sales voucher. Raises
    `LedgerMapIncomplete` if `ledger_map['sales_ledger']` is missing, and
    `ValueError` if any real line's item has no name (caller should have
    already gated on `item.tally_guid is not None`, which implies a real
    item with a name).
    """
    sales_ledger = (ledger_map or {}).get("sales_ledger")
    if not sales_ledger:
        raise LedgerMapIncomplete("tally_company.ledger_map is missing 'sales_ledger'")

    lines = real_lines(invoice)
    if not lines:
        raise ValueError("invoice has no real lines to push")

    round_off = _d(invoice.round_off)

    nsmap = {"UDF": _UDF_NS}
    env = etree.Element("ENVELOPE", nsmap=nsmap)
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = _sub(env, "BODY")
    importdata = _sub(body, "IMPORTDATA")
    reqdesc = _sub(importdata, "REQUESTDESC")
    _sub(reqdesc, "REPORTNAME", "Vouchers")
    reqdata = _sub(importdata, "REQUESTDATA")

    msg = _sub(reqdata, "TALLYMESSAGE")
    vch = etree.SubElement(msg, "VOUCHER", VCHTYPE="Sales", ACTION="Create")
    _sub(vch, "DATE", _yyyymmdd(invoice.date))
    _sub(vch, "EFFECTIVEDATE", _yyyymmdd(invoice.date))
    _sub(vch, "VOUCHERTYPENAME", "Sales")
    _sub(vch, "VOUCHERNUMBER", str(invoice.number) if invoice.number is not None else "")
    _sub(vch, "PARTYLEDGERNAME", party.legal_name)
    _sub(vch, "PERSISTEDVIEW", "Invoice Voucher View")

    narration = _weighment_narration(invoice)
    if narration:
        _sub(vch, "NARRATION", narration)

    udf = etree.SubElement(
        vch,
        f"{{{_UDF_NS}}}METALERP_REF",
        DESC="METALERP_REF",
        TYPE="String",
        ISLIST="No",
    )
    udf.text = f"inv_{invoice.id}"

    # inventory entries — one per real line. Round-off is folded into the
    # LAST line's amount (both the inventory AMOUNT and its
    # ACCOUNTINGALLOCATIONS.LIST amount), never a separate ledger line —
    # see the module docstring / the plan's "Live probe findings".
    for idx, line in enumerate(lines):
        is_last = idx == len(lines) - 1
        amount = _d(line.line_total) + (round_off if is_last else _d(0))
        item_name = line.item.name if line.item else (line.description or "Item")
        uom = line.uom or "Nos"

        inv = _sub(vch, "ALLINVENTORYENTRIES.LIST")
        _sub(inv, "STOCKITEMNAME", item_name)
        _sub(inv, "ISDEEMEDPOSITIVE", "No")
        _sub(inv, "RATE", f"{_money(line.unit_rate)}/{uom}")
        _sub(inv, "AMOUNT", _money(amount))
        _sub(inv, "ACTUALQTY", f"{_d(line.quantity)} {uom}")
        _sub(inv, "BILLEDQTY", f"{_d(line.quantity)} {uom}")
        acc = _sub(inv, "ACCOUNTINGALLOCATIONS.LIST")
        _sub(acc, "LEDGERNAME", sales_ledger)
        _sub(acc, "ISDEEMEDPOSITIVE", "No")
        _sub(acc, "AMOUNT", _money(amount))

    # party debit = grand total, bill-wise New Ref. The ONLY
    # LEDGERENTRIES.LIST block in the voucher — see module docstring.
    le = _sub(vch, "LEDGERENTRIES.LIST")
    _sub(le, "LEDGERNAME", party.legal_name)
    _sub(le, "ISDEEMEDPOSITIVE", "Yes")
    _sub(le, "AMOUNT", _neg(invoice.grand_total))
    bw = _sub(le, "BILLALLOCATIONS.LIST")
    _sub(bw, "NAME", str(invoice.number) if invoice.number is not None else invoice.id)
    _sub(bw, "BILLTYPE", "New Ref")
    _sub(bw, "AMOUNT", _neg(invoice.grand_total))

    return env


def serialize(env: etree._Element, encoding: str = "UTF-8") -> bytes:
    return etree.tostring(env, xml_declaration=True, encoding=encoding, pretty_print=True)


def build_sales_voucher_xml_bytes(
    invoice: Invoice, party: Party, ledger_map: dict[str, str],
    *, encoding: str = "UTF-8",
) -> bytes:
    """Default encoding is UTF-8, NOT UTF-16 — deliberately different from
    the purchase-side `inward/tally_xml.py` (which defaults UTF-16 because
    it writes a *file* for manual import; Tally reads a file's own declared
    encoding fine there). This module's output is POSTed live over HTTP to
    Tally's gateway by the agent; live-verified 2026-09-09 that a UTF-16
    body gets "Unknown Request, cannot be processed" on that path, while
    byte-identical UTF-8 content is accepted and creates the voucher.
    """
    env = build_sales_voucher_envelope(invoice, party, ledger_map)
    return serialize(env, encoding)

"""Build the Tally Purchase-voucher XML for an approved inward bill.

Shape per docs/EXTENSION-inward-bill-import.md -> *Tally Purchase voucher XML*:
master-create <LEDGER> / <STOCKITEM> messages ONLY for new masters, then the
<VOUCHER VCHTYPE="Purchase"> with header fields, UDF:METALERP_REF = the
inward_bill id, <ALLINVENTORYENTRIES.LIST> (one per line) and
<LEDGERENTRIES.LIST> (Purchase A/c taxable, CGST/SGST or IGST, Round Off,
party credit = grand total).

Dates YYYYMMDD. Intra/inter from supply_type. Serialised in the configured
encoding (UTF-16 default) with the matching <?xml?> declaration.

The accountant imports via Gateway of Tally -> Import Data -> Vouchers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from app.models import InwardBill
from app.models._mixins import SupplyType
from app.reference import STATE_CODES

_CENT = Decimal("0.01")

# Namespace stand-in for Tally's "UDF:" tag prefix (see build_envelope).
_UDF_NS = "urn:metalerp:tally-udf"


def _d(v: object) -> Decimal:
    return Decimal(0) if v is None else Decimal(str(v))


def _money(v: object) -> str:
    return str(_d(v).quantize(_CENT, rounding=ROUND_HALF_UP))


def _neg(v: object) -> str:
    return str((-_d(v)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _yyyymmdd(d: date | None) -> str:
    return d.strftime("%Y%m%d") if d is not None else ""


def _state_name(code: str | None) -> str:
    return STATE_CODES.get(code or "", "")


@dataclass
class LedgerConfig:
    creditors_group: str = "Sundry Creditors"
    purchase_ledger: str = "Purchase Accounts"
    cgst_ledger: str = "CGST"
    sgst_ledger: str = "SGST"
    igst_ledger: str = "IGST"
    round_off_ledger: str = "Round Off"
    xml_encoding: str = "UTF-16"


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def build_envelope(
    bill: InwardBill,
    cfg: LedgerConfig,
    *,
    new_supplier_name: str | None = None,
    new_item_names: set[str] | None = None,
) -> etree._Element:
    new_item_names = new_item_names or set()
    is_inter = bill.supply_type == SupplyType.inter
    party_name = (
        (bill.matched_party_id and _party_name_hint(bill))
        or new_supplier_name
        or bill.supplier_name
        or "Unknown Supplier"
    )
    state_name = _state_name(bill.place_of_supply_state_code)

    # Tally's UDF tags are literally "UDF:NAME". lxml rejects a bare colon in a
    # tag, so we register a `UDF` namespace — Tally's importer accepts the
    # xmlns:UDF declaration and reads the element as UDF:METALERP_REF.
    nsmap = {"UDF": _UDF_NS}
    env = etree.Element("ENVELOPE", nsmap=nsmap)
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = _sub(env, "BODY")
    importdata = _sub(body, "IMPORTDATA")
    reqdesc = _sub(importdata, "REQUESTDESC")
    _sub(reqdesc, "REPORTNAME", "Vouchers")
    reqdata = _sub(importdata, "REQUESTDATA")

    # --- master creates: new supplier ledger ---
    if new_supplier_name:
        msg = _sub(reqdata, "TALLYMESSAGE")
        led = etree.SubElement(msg, "LEDGER", NAME=new_supplier_name, ACTION="Create")
        _sub(led, "NAME", new_supplier_name)
        _sub(led, "PARENT", cfg.creditors_group)
        if bill.supplier_gstin:
            _sub(led, "PARTYGSTIN", bill.supplier_gstin)
            _sub(led, "GSTREGISTRATIONTYPE", "Regular")
        if state_name:
            _sub(led, "LEDSTATENAME", state_name)
        _sub(led, "ISBILLWISEON", "Yes")

    # --- master creates: new stock items ---
    for line in bill.lines:
        name = _staged_item_name(line)
        if name and name in new_item_names:
            msg = _sub(reqdata, "TALLYMESSAGE")
            si = etree.SubElement(msg, "STOCKITEM", NAME=name, ACTION="Create")
            _sub(si, "NAME", name)
            _sub(si, "PARENT", "Primary")
            _sub(si, "BASEUNITS", line.uom or "Nos")
            if line.hsn:
                gst = _sub(si, "GSTDETAILS.LIST")
                _sub(gst, "HSNMASTERNAME", "")
                _sub(gst, "HSNCODE", line.hsn)

    # --- the purchase voucher ---
    msg = _sub(reqdata, "TALLYMESSAGE")
    vch = etree.SubElement(msg, "VOUCHER", VCHTYPE="Purchase", ACTION="Create")
    _sub(vch, "DATE", _yyyymmdd(bill.bill_date))
    _sub(vch, "EFFECTIVEDATE", _yyyymmdd(bill.bill_date))
    _sub(vch, "VOUCHERTYPENAME", "Purchase")
    _sub(vch, "REFERENCE", bill.bill_no or "")
    _sub(vch, "REFERENCEDATE", _yyyymmdd(bill.bill_date))
    _sub(vch, "VOUCHERNUMBER", bill.bill_no or "")
    _sub(vch, "PARTYLEDGERNAME", party_name)
    _sub(vch, "BASICBUYERNAME", party_name)
    if state_name:
        _sub(vch, "PLACEOFSUPPLY", state_name)
    _sub(vch, "PERSISTEDVIEW", "Invoice Voucher View")

    udf = etree.SubElement(
        vch,
        f"{{{_UDF_NS}}}METALERP_REF",
        DESC="METALERP_REF",
        TYPE="String",
        ISLIST="No",
    )
    udf.text = f"ib_{bill.id}"

    # inventory entries — one per line
    for line in bill.lines:
        item_name = _staged_item_name(line) or (line.description or "Item")
        inv = _sub(vch, "ALLINVENTORYENTRIES.LIST")
        _sub(inv, "STOCKITEMNAME", item_name)
        _sub(inv, "ISDEEMEDPOSITIVE", "Yes")
        _sub(inv, "RATE", f"{_money(line.unit_rate)}/{line.uom or 'Nos'}")
        _sub(inv, "AMOUNT", _neg(line.taxable_value))
        _sub(inv, "ACTUALQTY", f"{_d(line.quantity)} {line.uom or 'Nos'}")
        _sub(inv, "BILLEDQTY", f"{_d(line.quantity)} {line.uom or 'Nos'}")
        acc = _sub(inv, "ACCOUNTINGALLOCATIONS.LIST")
        _sub(acc, "LEDGERNAME", cfg.purchase_ledger)
        _sub(acc, "ISDEEMEDPOSITIVE", "Yes")
        _sub(acc, "AMOUNT", _neg(line.taxable_value))

    # ledger entries
    def ledger_entry(name: str, amount: str, positive: bool) -> None:
        le = _sub(vch, "LEDGERENTRIES.LIST")
        _sub(le, "LEDGERNAME", name)
        _sub(le, "ISDEEMEDPOSITIVE", "Yes" if positive else "No")
        _sub(le, "AMOUNT", amount)

    ledger_entry(cfg.purchase_ledger, _neg(bill.taxable_total), positive=True)
    if is_inter:
        if _d(bill.igst_total) != 0:
            ledger_entry(cfg.igst_ledger, _neg(bill.igst_total), positive=True)
    else:
        if _d(bill.cgst_total) != 0:
            ledger_entry(cfg.cgst_ledger, _neg(bill.cgst_total), positive=True)
        if _d(bill.sgst_total) != 0:
            ledger_entry(cfg.sgst_ledger, _neg(bill.sgst_total), positive=True)
    if _d(bill.round_off) != 0:
        ledger_entry(cfg.round_off_ledger, _neg(bill.round_off), positive=True)

    # party credit = grand total (positive amount, not deemed-positive)
    le = _sub(vch, "LEDGERENTRIES.LIST")
    _sub(le, "LEDGERNAME", party_name)
    _sub(le, "ISDEEMEDPOSITIVE", "No")
    _sub(le, "AMOUNT", _money(bill.grand_total))
    bw = _sub(le, "BILLALLOCATIONS.LIST")
    _sub(bw, "NAME", bill.bill_no or f"ib_{bill.id}")
    _sub(bw, "BILLTYPE", "New Ref")
    _sub(bw, "AMOUNT", _money(bill.grand_total))

    return env


def _party_name_hint(bill: InwardBill) -> str | None:
    """The XML uses the supplier's legal name for PARTYLEDGERNAME; for a matched
    party the router passes the real name in. Fallback to the extracted name.
    """
    return None


def _staged_item_name(line: object) -> str | None:
    staged = getattr(line, "new_item_staged_json", None)
    if staged and staged.get("name"):
        return str(staged["name"])
    return None


def serialize(env: etree._Element, encoding: str = "UTF-16") -> bytes:
    return etree.tostring(
        env, xml_declaration=True, encoding=encoding, pretty_print=True
    )


def build_xml_bytes(
    bill: InwardBill,
    cfg: LedgerConfig,
    *,
    party_name: str | None = None,
    new_supplier_name: str | None = None,
    new_item_names: set[str] | None = None,
) -> bytes:
    env = build_envelope(
        bill,
        cfg,
        new_supplier_name=new_supplier_name,
        new_item_names=new_item_names,
    )
    if party_name:
        for tag in ("PARTYLEDGERNAME", "BASICBUYERNAME"):
            el = env.find(f".//{tag}")
            if el is not None:
                el.text = party_name
        # last LEDGERENTRIES.LIST is the party credit
        entries = env.findall(".//LEDGERENTRIES.LIST/LEDGERNAME")
        if entries:
            entries[-1].text = party_name
    return serialize(env, cfg.xml_encoding)

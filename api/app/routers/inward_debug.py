"""Dev-only debug endpoint: PDF in -> Tally XML out, no auth, no DB writes.

Mounted only when APP_ENV != production (see main.py). It runs the real
extractor + reconcile + XML builder against an uploaded PDF and streams back
the XML, so you can round-trip a supplier PDF into Tally Prime in seconds
without going through upload / review / approve.

  GET  /api/inward-debug           -> a tiny HTML form
  POST /api/inward-debug/xml       -> multipart PDF -> .xml download
  POST /api/inward-debug/extract   -> multipart PDF -> JSON (what the parser saw)

Line resolution is skipped here (no tenant catalogue) — every line is treated
as a new stock item, which is exactly what a first import into a fresh Tally
company needs.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.models import InwardBill, InwardBillLine
from app.models._mixins import SupplyType
from app.services.inward import einvoice_qr, extract_text
from app.services.inward.reconcile import reconcile
from app.services.inward.tally_xml import LedgerConfig, build_xml_bytes

router = APIRouter(prefix="/api/inward-debug", tags=["inward-debug"])

_FORM_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Inward debug — PDF → Tally XML</title>
<style>
 body{font:14px/1.5 system-ui,sans-serif;max-width:640px;margin:64px auto;
      padding:0 20px;color:#23201b;background:#f4f1ec}
 h1{font-size:20px}
 .card{background:#fff;border:1px solid #e6e0d6;border-radius:10px;
       padding:20px;margin:16px 0}
 input[type=file]{display:block;margin:12px 0}
 button{background:#2f6f89;color:#fff;border:0;border-radius:7px;
        padding:8px 16px;font:inherit;cursor:pointer;margin-right:8px}
 code{background:#efe9df;padding:1px 5px;border-radius:4px}
 .muted{color:#8a8272;font-size:12px}
</style></head><body>
<h1>Inward debug — PDF → Tally XML</h1>
<p class="muted">Dev-only. Runs the real extractor + reconciliation + Tally Purchase-voucher
builder on an uploaded supplier PDF. No login, nothing saved.</p>
<div class="card">
 <form action="/api/inward-debug/xml" method="post" enctype="multipart/form-data">
  <label><b>Download Tally XML</b></label>
  <input type="file" name="file" accept="application/pdf" required>
  <button type="submit">Get inward-*.xml</button>
  <span class="muted">→ Gateway of Tally → Import Data → Vouchers</span>
 </form>
</div>
<div class="card">
 <form action="/api/inward-debug/extract" method="post" enctype="multipart/form-data">
  <label><b>Inspect what the parser saw</b> (JSON)</label>
  <input type="file" name="file" accept="application/pdf" required>
  <button type="submit">Extract → JSON</button>
 </form>
</div>
</body></html>
"""


@router.get("", response_class=HTMLResponse)
def form() -> str:
    return _FORM_HTML


def _pdf_to_bill(data: bytes, filename: str) -> InwardBill:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        qr = einvoice_qr.decode(tmp_path)
        raw = extract_text.extract(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if qr is not None:
        raw.supplier_gstin = qr.supplier_gstin or raw.supplier_gstin
        raw.buyer_gstin = qr.buyer_gstin or raw.buyer_gstin
        raw.bill_no = qr.bill_no or raw.bill_no

    supplier_prefix = raw.supplier_gstin[:2] if raw.supplier_gstin else None
    buyer_prefix = raw.buyer_gstin[:2] if raw.buyer_gstin else None
    supply_type = None
    if supplier_prefix and buyer_prefix:
        supply_type = (
            SupplyType.intra if supplier_prefix == buyer_prefix else SupplyType.inter
        )

    from datetime import date as _date

    bill = InwardBill(
        id=str(uuid.uuid4()),
        tenant_id="debug",
        source_filename=filename,
        supplier_name=raw.supplier_name,
        supplier_gstin=raw.supplier_gstin,
        bill_no=raw.bill_no,
        bill_date=_date.fromisoformat(raw.bill_date) if raw.bill_date else None,
        sales_order_ref=raw.sales_order_ref,
        place_of_supply_state_code=raw.place_of_supply_state_code or supplier_prefix,
        supply_type=supply_type,
        taxable_total=raw.taxable_total,
        cgst_total=raw.cgst_total,
        sgst_total=raw.sgst_total,
        igst_total=raw.igst_total,
        round_off=raw.round_off,
        grand_total=raw.grand_total,
        amount_in_words=raw.amount_in_words,
        raw_text=raw.raw_text,
    )
    for rl in raw.lines:
        bill.lines.append(
            InwardBillLine(
                sl_no=rl.sl_no,
                description=rl.description,
                hsn=rl.hsn,
                quantity=rl.quantity,
                uom=rl.uom,
                unit_rate=rl.unit_rate,
                discount_pct=rl.discount_pct,
                taxable_value=rl.line_total,
                cgst_rate=rl.cgst_rate,
                cgst_amt=rl.cgst_amt,
                sgst_rate=rl.sgst_rate,
                sgst_amt=rl.sgst_amt,
                line_total=rl.line_total,
                # treat every line as a new stock item for the debug XML
                new_item_staged_json={"name": rl.description},
            )
        )
    return bill


def _check_pdf(file: UploadFile) -> bytes:
    data = file.file.read()
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF only")
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    return data


@router.post("/xml")
def to_xml(file: UploadFile = File(...)) -> Response:
    data = _check_pdf(file)
    bill = _pdf_to_bill(data, file.filename or "upload.pdf")

    recon = reconcile(
        taxable_total=bill.taxable_total,
        cgst_total=bill.cgst_total,
        sgst_total=bill.sgst_total,
        igst_total=bill.igst_total,
        round_off=bill.round_off,
        grand_total=bill.grand_total,
    )

    cfg = LedgerConfig()
    new_names: set[str] = {
        name
        for line in bill.lines
        if (name := (line.new_item_staged_json or {}).get("name"))
    }
    xml_bytes = build_xml_bytes(
        bill,
        cfg,
        party_name=bill.supplier_name,
        new_supplier_name=bill.supplier_name,
        new_item_names=new_names,
    )
    fname = f"inward-{bill.bill_no or 'debug'}.xml"
    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Reconciled": str(recon.reconciled).lower(),
            "X-Reconcile-Discrepancy": str(recon.discrepancy),
        },
    )


@router.post("/extract")
def to_json(file: UploadFile = File(...)) -> JSONResponse:
    data = _check_pdf(file)
    bill = _pdf_to_bill(data, file.filename or "upload.pdf")
    recon = reconcile(
        taxable_total=bill.taxable_total,
        cgst_total=bill.cgst_total,
        sgst_total=bill.sgst_total,
        igst_total=bill.igst_total,
        round_off=bill.round_off,
        grand_total=bill.grand_total,
    )
    return JSONResponse(
        {
            "supplier_name": bill.supplier_name,
            "supplier_gstin": bill.supplier_gstin,
            "bill_no": bill.bill_no,
            "bill_date": bill.bill_date.isoformat() if bill.bill_date else None,
            "place_of_supply_state_code": bill.place_of_supply_state_code,
            "supply_type": bill.supply_type.value if bill.supply_type else None,
            "totals": {
                "taxable_total": str(bill.taxable_total),
                "cgst_total": str(bill.cgst_total),
                "sgst_total": str(bill.sgst_total),
                "igst_total": str(bill.igst_total),
                "round_off": str(bill.round_off),
                "grand_total": str(bill.grand_total),
            },
            "reconciled": recon.reconciled,
            "reconcile_discrepancy": str(recon.discrepancy),
            "lines": [
                {
                    "sl_no": line.sl_no,
                    "description": line.description,
                    "hsn": line.hsn,
                    "quantity": str(line.quantity),
                    "uom": line.uom,
                    "unit_rate": str(line.unit_rate),
                    "discount_pct": str(line.discount_pct),
                    "cgst_amt": str(line.cgst_amt),
                    "sgst_amt": str(line.sgst_amt),
                    "line_total": str(line.line_total),
                }
                for line in bill.lines
            ],
        }
    )

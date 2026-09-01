"""Extraction orchestrator.

POST /api/inward-bills (multipart) -> create InwardBill (uploaded) ->
synchronously for a single small PDF:
  1. e-invoice QR?  (money fields from the QR if present; else None)
  2. supplier_template for this GSTIN?  (X6 — skipped for now)
  3. table-extract the line grid + regex header/totals
  4. reconcile: taxable + CGST + SGST + IGST + round_off == grand (±0.05)
  5. resolve supplier (GSTIN key / stage-new)
  6. resolve every line (fuzzy ladder / stage-new)
  7. set status: needs_review if unreconciled OR any low-confidence step,
     else needs_review anyway (a human always eyeballs the first pass).

Every attempt is logged to `extraction_run`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import ExtractionRun, InwardBill, InwardBillLine
from app.models._mixins import ExtractionMethod, InwardStatus
from app.services.inward import einvoice_qr, extract_text
from app.services.inward.reconcile import reconcile
from app.services.inward.resolve_lines import resolve_lines
from app.services.inward.resolve_supplier import resolve_supplier


def _to_decimal(v: object) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _next_attempt(session: Session, bill_id: str) -> int:
    return (
        session.query(ExtractionRun)
        .filter(ExtractionRun.inward_bill_id == bill_id)
        .count()
        + 1
    )


def run_extraction(session: Session, bill: InwardBill) -> InwardBill:
    """Populate `bill` (and its lines) from its source PDF. Caller commits."""
    bill.status = InwardStatus.extracting
    session.flush()

    attempt = _next_attempt(session, bill.id)
    method = ExtractionMethod.table
    error: str | None = None
    confidence = 0.0

    try:
        pdf_path = bill.source_pdf_path or ""
        qr = einvoice_qr.decode(pdf_path)
        raw = extract_text.extract(pdf_path)
        bill.raw_text = raw.raw_text

        if raw.low_text and not raw.lines:
            # X7 image path not built yet.
            bill.status = InwardStatus.needs_review
            bill.error_message = "image path unsupported (scanned PDF) — retry after X7"
            _log_run(session, bill.id, attempt, "image", ok=False, conf=0.0,
                     err=bill.error_message)
            session.flush()
            return bill

        if qr is not None:
            method = ExtractionMethod.einvoice_qr
            raw.supplier_gstin = qr.supplier_gstin or raw.supplier_gstin
            raw.buyer_gstin = qr.buyer_gstin or raw.buyer_gstin
            raw.bill_no = qr.bill_no or raw.bill_no
            if qr.grand_total:
                raw.grand_total = _to_decimal(qr.grand_total)

        _apply_header(bill, raw)
        _apply_totals(bill, raw)
        _apply_lines(session, bill, raw)

        recon = reconcile(
            taxable_total=bill.taxable_total,
            cgst_total=bill.cgst_total,
            sgst_total=bill.sgst_total,
            igst_total=bill.igst_total,
            round_off=bill.round_off,
            grand_total=bill.grand_total,
        )
        bill.reconciled = recon.reconciled
        bill.reconcile_discrepancy = recon.discrepancy

        # supplier
        addr = raw.supplier_address
        sup = resolve_supplier(
            session,
            bill.tenant_id,
            supplier_name=raw.supplier_name,
            supplier_gstin=raw.supplier_gstin,
            buyer_gstin=raw.buyer_gstin,
            place_of_supply_state_code=raw.place_of_supply_state_code,
            supplier_phone=raw.supplier_phone,
            address_block=(
                {
                    "line1": addr.line1,
                    "line2": addr.line2,
                    "city": addr.city,
                    "state_code": addr.state_code,
                    "pincode": addr.pincode,
                }
                if addr is not None
                else None
            ),
        )
        bill.matched_party_id = sup.matched_party_id
        bill.new_supplier_staged_json = sup.new_supplier_staged
        bill.supply_type = sup.supply_type
        bill.place_of_supply_state_code = sup.place_of_supply_state_code

        # lines
        line_res = resolve_lines(session, bill.tenant_id, list(bill.lines))
        by_sl = {lr.sl_no: lr for lr in line_res}
        for line in bill.lines:
            lr = by_sl.get(line.sl_no)
            if lr is None:
                continue
            line.match_method = lr.match_method
            line.match_confidence = (
                Decimal(str(lr.match_confidence))
                if lr.match_confidence is not None
                else None
            )
            line.matched_item_id = lr.matched_item_id
            line.new_item_staged_json = lr.new_item_staged
            line.review_flag = lr.review_flag

        # confidence: field average, docked for unreconciled
        fc = raw.field_confidence
        confidence = round(sum(fc.values()) / max(len(fc), 1), 3)
        if not recon.reconciled:
            confidence = min(confidence, 0.4)

        bill.extraction_method = method
        bill.extraction_confidence = Decimal(str(confidence))
        # First pass always lands in review.
        bill.status = InwardStatus.needs_review
        _log_run(session, bill.id, attempt, method.value, ok=True, conf=confidence)

    except Exception as exc:  # never a 500 to the caller
        error = f"{type(exc).__name__}: {exc}"
        bill.status = InwardStatus.error
        bill.error_message = error
        _log_run(session, bill.id, attempt, method.value, ok=False, conf=0.0, err=error)

    session.flush()
    return bill


def _apply_header(bill: InwardBill, raw: extract_text.RawExtraction) -> None:
    bill.supplier_name = raw.supplier_name
    bill.supplier_gstin = raw.supplier_gstin
    if raw.supplier_gstin:
        bill.supplier_state_code = raw.supplier_gstin[:2]
    if raw.supplier_address and raw.supplier_address.state_code:
        bill.supplier_state_code = raw.supplier_address.state_code
    bill.bill_no = raw.bill_no
    if raw.bill_date:
        bill.bill_date = date.fromisoformat(raw.bill_date)
    bill.sales_order_ref = raw.sales_order_ref
    bill.place_of_supply_state_code = raw.place_of_supply_state_code
    bill.amount_in_words = raw.amount_in_words


def _apply_totals(bill: InwardBill, raw: extract_text.RawExtraction) -> None:
    bill.taxable_total = raw.taxable_total
    bill.cgst_total = raw.cgst_total
    bill.sgst_total = raw.sgst_total
    bill.igst_total = raw.igst_total
    bill.round_off = raw.round_off
    bill.grand_total = raw.grand_total


def _apply_lines(
    session: Session, bill: InwardBill, raw: extract_text.RawExtraction
) -> None:
    bill.lines.clear()
    session.flush()
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
                igst_rate=rl.igst_rate,
                igst_amt=rl.igst_amt,
                line_total=rl.line_total,
            )
        )
    session.flush()


def _log_run(
    session: Session,
    bill_id: str,
    attempt: int,
    method: str,
    *,
    ok: bool,
    conf: float,
    err: str | None = None,
) -> None:
    session.add(
        ExtractionRun(
            inward_bill_id=bill_id,
            attempt=attempt,
            method=method,
            ok=ok,
            confidence=conf,
            error=err,
        )
    )

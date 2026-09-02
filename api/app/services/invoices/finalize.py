"""Finalize a draft invoice — one transaction.

  1. gate: party set, >=1 line, every line qty>0 & rate>0        (else FinalizeError)
  2. claim number from number_sequence (SELECT ... FOR UPDATE), gap-free
  3. compute_invoice() -> freeze subtotal / discount_total / taxable_total /
     round_off / grand_total / amount_in_words / template_version; snapshot
     the tenant's terms + declaration text
  4. per line: resolve description -> item (exact/alias/fuzzy ladder); if no
     match, create item (source=auto_from_invoice, status=unconfirmed);
     else bump last_rate / last_sold_at / times_billed. Freeze line_total.
  5. Loop 2: learn_from_invoice(...) — group/category backfill + aliases
  6. party.last_txn_at = max(current, invoice.date)  (forward-only)
  7. status = final; audit_log
  8. PDF render (best-effort — a raise leaves status=final, pdf_status=failed)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.domain.tax import InvoiceInput, LineInput, compute_invoice
from app.domain.weighment import LineMeasure, compute_measure
from app.models import (
    AuditLog,
    Invoice,
    Item,
    NumberSequence,
    Party,
    Tenant,
)
from app.models._mixins import (
    InvoiceStatus,
    ItemSource,
    PdfStatus,
)
from app.services.catalogue.classify_apply import Classifier
from app.services.catalogue.learn_from_invoice import learn_from_invoice
from app.services.invoices.common import finalize_blockers
from app.services.item_resolution import resolve_item


class FinalizeError(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class FinalizeResult:
    number: int
    fy: str
    created_item_ids: list[str] = field(default_factory=list)
    learned_group_ids: list[str] = field(default_factory=list)
    pdf_status: PdfStatus = PdfStatus.none


def _close_open_segments(invoice: Invoice) -> None:
    """Fill in `weighment_slips` for every segment present on the lines that
    the operator didn't record a scale weight for, using the line-derived
    kg. Result: one slip per segment, ordered.
    """
    ordered = sorted(invoice.lines, key=lambda x: x.sl_no)
    if not ordered:
        invoice.weighment_slips = None
        return
    measure = compute_measure(
        [
            LineMeasure(
                quantity=Decimal(str(ln.quantity or 0)),
                uom=ln.uom,
                segment_no=ln.segment_no or 1,
            )
            for ln in ordered
        ],
        slips=invoice.weighment_slips or [],
    )
    recorded = {
        int(s["seg"]): s["recorded_kg"]
        for s in (invoice.weighment_slips or [])
        if "seg" in s
    }
    invoice.weighment_slips = [
        {
            "seg": seg.seg,
            "recorded_kg": str(recorded.get(seg.seg, seg.weight_kg)),
        }
        for seg in measure.segments
    ]


def _claim_number(session: Session, tenant_id: str, series: str, fy: str) -> int:
    """Increment (tenant, series, fy).last_value under a row lock. Gap-free:
    the increment lives in the same transaction as the finalize, so a
    rollback releases the number.
    """
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
    stmt = select(NumberSequence).where(
        NumberSequence.tenant_id == tenant_id,
        NumberSequence.series == series,
        NumberSequence.fy == fy,
    )
    if is_pg:
        stmt = stmt.with_for_update()
    row = session.scalar(stmt)
    if row is None:
        row = NumberSequence(tenant_id=tenant_id, series=series, fy=fy, last_value=0)
        session.add(row)
        session.flush()
        if is_pg:
            row = session.scalar(stmt)  # re-select under lock
    assert row is not None
    row.last_value += 1
    session.flush()
    return row.last_value


def finalize_invoice(
    session: Session, invoice: Invoice, *, actor_user_id: str | None
) -> FinalizeResult:
    reasons = finalize_blockers(invoice)
    if reasons:
        raise FinalizeError(reasons)

    now = datetime.now(UTC)
    synonyms = load_synonym_map(session, invoice.tenant_id)

    real_lines = [ln for ln in invoice.lines if (ln.description or "").strip()]

    # --- 2. number ---
    number = _claim_number(session, invoice.tenant_id, invoice.series, invoice.fy)
    invoice.number = number

    # --- 3. totals ---
    computed = compute_invoice(
        InvoiceInput(
            lines=[
                LineInput(
                    quantity=Decimal(str(ln.quantity or 0)),
                    unit_rate=Decimal(str(ln.unit_rate or 0)),
                    discount=Decimal(str(ln.discount or 0)),
                )
                for ln in real_lines
            ],
            invoice_discount=Decimal(str(invoice.invoice_discount or 0)),
        )
    )
    invoice.subtotal = computed.subtotal
    invoice.discount_total = computed.discount_total
    invoice.taxable_total = computed.taxable_total
    invoice.round_off = computed.round_off
    invoice.grand_total = computed.grand_total
    invoice.amount_in_words = computed.amount_in_words
    invoice.template_version = "v1-nongst"

    # weighment: any segment the operator left open (no recorded slip) is
    # closed here at its line-derived weight, so a finalized bill always
    # has one recorded figure per segment.
    _close_open_segments(invoice)

    tenant = session.get(Tenant, invoice.tenant_id)
    if tenant is not None:
        invoice.terms_snapshot = tenant.terms_text
        invoice.declaration_snapshot = tenant.declaration_text

    # --- 4. resolve / create items, freeze line totals ---
    classifier = Classifier(session, invoice.tenant_id)
    created_item_ids: list[str] = []
    for line, cl in zip(real_lines, computed.lines, strict=True):
        line.line_total = cl.line_total

        if line.item_id is None:
            match = resolve_item(
                session, invoice.tenant_id, line.description, line.hsn_code,
                synonyms=synonyms,
            )
            if match.item_id is not None:
                line.item_id = match.item_id
            else:
                key = normalize_name(line.description, synonyms)
                existing = (
                    session.scalar(
                        select(Item).where(
                            Item.tenant_id == invoice.tenant_id,
                            Item.name_normalized == key,
                        )
                    )
                    if key
                    else None
                )
                if existing is not None:
                    line.item_id = existing.id
                else:
                    applied = classifier.apply(
                        line.description, hsn=line.hsn_code, uom=line.uom
                    )
                    item = Item(
                        tenant_id=invoice.tenant_id,
                        name=line.description.strip()[:300],
                        name_normalized=key or line.description.strip().lower()[:300],
                        uom=line.uom,
                        hsn_code=line.hsn_code,
                        rate_mode="piece",
                        group_id=applied.group_id,
                        category_id=applied.category_id,
                        source=ItemSource.auto_from_invoice,
                        status=applied.status,
                    )
                    session.add(item)
                    session.flush()
                    line.item_id = item.id
                    created_item_ids.append(item.id)

        # bump sales stats on the linked item
        it = session.get(Item, line.item_id) if line.item_id else None
        if it is not None:
            if line.unit_rate is not None:
                it.last_rate = float(line.unit_rate)
            it.last_sold_at = now
            it.times_billed = (it.times_billed or 0) + 1

    # --- 5. Loop 2: learn categories from the linked products ---
    learn = learn_from_invoice(
        session,
        invoice.tenant_id,
        real_lines,
        created_item_ids=set(created_item_ids),
        now=now,
    )

    # --- 6. party.last_txn_at (forward-only) ---
    party = session.get(Party, invoice.party_id)
    if party is not None:
        inv_dt = datetime(
            invoice.date.year, invoice.date.month, invoice.date.day, tzinfo=UTC
        )
        current = party.last_txn_at
        if current is not None and current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if current is None or current < inv_dt:
            party.last_txn_at = inv_dt

    # --- 7. status + audit ---
    invoice.status = InvoiceStatus.final
    session.add(
        AuditLog(
            tenant_id=invoice.tenant_id,
            actor_user_id=actor_user_id,
            entity="invoice",
            entity_id=invoice.id,
            action="finalize",
            after_json={
                "number": number,
                "fy": invoice.fy,
                "grand_total": str(invoice.grand_total),
                "created_item_ids": created_item_ids,
                "learned_group_ids": learn.learned_group_ids,
                "created_category_ids": learn.created_category_ids,
            },
        )
    )
    session.flush()

    # --- 8. PDF (best-effort) ---
    pdf_status = _render_pdf_best_effort(session, invoice)

    return FinalizeResult(
        number=number,
        fy=invoice.fy,
        created_item_ids=created_item_ids,
        learned_group_ids=learn.learned_group_ids,
        pdf_status=pdf_status,
    )


def _render_pdf_best_effort(session: Session, invoice: Invoice) -> PdfStatus:
    """Render synchronously; on any failure the finalize still stands and
    the invoice is flagged for a manual re-render.
    """
    try:
        from app.services.invoices.pdf import render_invoice_pdf

        render_invoice_pdf(session, invoice)
        return invoice.pdf_status
    except Exception as exc:  # noqa: BLE001 - deliberately broad; finalize must not roll back
        invoice.pdf_status = PdfStatus.failed
        invoice.pdf_path = None
        # leave a breadcrumb without failing the request
        session.add(
            AuditLog(
                tenant_id=invoice.tenant_id,
                actor_user_id=None,
                entity="invoice",
                entity_id=invoice.id,
                action="pdf_render_failed",
                after_json={"error": repr(exc)[:500]},
            )
        )
        session.flush()
        return PdfStatus.failed

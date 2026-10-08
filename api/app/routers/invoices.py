"""Sales invoice API — /api/invoices, scoped to the caller's tenant.

A draft is created without a number and edited as a whole (`PUT` replaces
header + all lines). `POST /{id}/finalize` runs the finalize transaction
(number claim, freeze totals, item accretion, Loop-2 category learning,
PDF). Finalized invoices are immutable; `cancel` sets status without
reusing the number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DraftUser, SessionDep, WriteUser
from app.domain.normalize import load_synonym_map
from app.models import (
    CustomerOrder,
    Invoice,
    InvoiceLine,
    Party,
    Payment,
    PaymentAllocation,
    TallySyncJob,
    User,
)
from app.models._mixins import (
    AllocationType,
    DocType,
    InvoiceStatus,
    PaymentStatus,
    PdfStatus,
    UserRole,
)
from app.models.whatsapp import WhatsappMessage
from app.schemas_invoice import (
    DuplicateOut,
    FinalizeOut,
    InvoiceCreate,
    InvoiceLineIn,
    InvoiceLineOut,
    InvoiceListItem,
    InvoiceOut,
    InvoiceUpdate,
    InvoiceWhatsappMessageOut,
    PartyBrief,
    SlipCaptureOut,
    SlipLineReview,
    VoiceLineOut,
    VoiceLineTranscriptIn,
)
from app.services import order_notify
from app.services.invoices.common import (
    download_name,
    finalize_blockers,
    financial_year,
    measure_for,
    totals_for,
)
from app.services.invoices.finalize import FinalizeError, finalize_invoice
from app.services.item_resolution import resolve_item
from app.services.ocr.extract_slip import SlipExtractionError, extract_slip
from app.services.ocr.slip_to_draft import build_draft
from app.services.payments import paid_amount_for_invoice
from app.services.speech.keyterms import top_item_keyterms
from app.services.speech.parse_line import parse_voice_line
from app.services.speech.transcribe import TranscriptionError, transcribe

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

_MAX_SLIP_BYTES = 10 * 1024 * 1024

_EDITABLE_STATUSES = {InvoiceStatus.draft}


def _load(session: SessionDep, tenant_id: str, invoice_id: str, *, lock: bool = False) -> Invoice:
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
        .options(selectinload(Invoice.lines))
    )
    if lock and session.bind is not None and session.bind.dialect.name == "postgresql":
        # two saves at the same instant queue up, so the second sees the first's stamp
        stmt = stmt.with_for_update(of=Invoice)
    inv = session.scalar(stmt)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return inv


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _check_not_changed_by_someone_else(
    session: SessionDep, inv: Invoice, expected: datetime | None, user: User
) -> None:
    """Refuse a save made from a stale copy of the draft, naming who changed it and when.

    `expected` is the `updated_at` the editor loaded. A client that does not send it is not
    protected (old clients keep working); the web editor always sends it."""
    if expected is None or _utc_naive(expected) == _utc_naive(inv.updated_at):
        return
    editor = session.get(User, inv.last_edited_by_user_id) if inv.last_edited_by_user_id else None
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "draft_changed",
            "message": "Someone else changed this draft since you opened it.",
            "edited_by": (editor.email if editor and editor.id != user.id else None),
            "edited_by_you": bool(editor and editor.id == user.id),
            "edited_at": inv.updated_at.isoformat(),
        },
    )


def _owned_party(session: SessionDep, tenant_id: str, party_id: str) -> Party:
    p = session.scalar(select(Party).where(Party.id == party_id, Party.tenant_id == tenant_id))
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Party not found")
    return p


def _apply_lines(inv: Invoice, lines: list) -> None:
    inv.lines.clear()
    for i, ln in enumerate(lines, start=1):
        inv.lines.append(
            InvoiceLine(
                sl_no=i,
                item_id=ln.item_id,
                description=ln.description.strip(),
                hsn_code=ln.hsn_code,
                quantity=ln.quantity,
                uom=ln.uom,
                unit_rate=ln.unit_rate,
                discount=ln.discount or Decimal("0"),
                discount_pct=ln.discount_pct,
                size_pos=ln.size_pos,
                segment_no=getattr(ln, "segment_no", 1) or 1,
            )
        )


def _slips_payload(slips: list | None) -> list | None:
    """Normalise WeighmentSlipIn models to the JSON shape stored on the row,
    dropping any that don't map to a real segment on save-time renumber."""
    if not slips:
        return None
    return [{"seg": int(s.seg), "recorded_kg": str(s.recorded_kg)} for s in slips]


def _payment_fields(
    session: SessionDep, inv: Invoice
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """paid_amount / balance_due / payment_status — only computed for a
    finalized invoice (draft/cancelled have no frozen grand_total to bill
    against, so these stay None rather than reporting pointless zeros).
    """
    if inv.status != InvoiceStatus.final:
        return None, None, None
    total = inv.grand_total or Decimal("0")
    paid = paid_amount_for_invoice(session, inv.id)
    balance = total - paid
    if paid <= 0:
        label = "unpaid"
    elif paid >= total:
        label = "paid"
    else:
        label = "partial"
    return paid, balance, label


def _email(session: SessionDep, user_id: str | None) -> str | None:
    u = session.get(User, user_id) if user_id else None
    return u.email if u else None


def _out(session: SessionDep, inv: Invoice) -> InvoiceOut:
    party = session.get(Party, inv.party_id) if inv.party_id else None
    paid_amount, balance_due, payment_status = _payment_fields(session, inv)
    return InvoiceOut(
        id=inv.id,
        doc_type=inv.doc_type,
        series=inv.series,
        number=inv.number,
        fy=inv.fy,
        date=inv.date,
        status=inv.status,
        template_version=inv.template_version,
        party_id=inv.party_id,
        party=PartyBrief.model_validate(party) if party else None,
        bill_to_addr_id=inv.bill_to_addr_id,
        ship_to_addr_id=inv.ship_to_addr_id,
        notes=inv.notes,
        terms_snapshot=inv.terms_snapshot,
        declaration_snapshot=inv.declaration_snapshot,
        invoice_discount=inv.invoice_discount or Decimal("0"),
        totals=totals_for(inv),
        measure=measure_for(inv),
        pdf_status=inv.pdf_status,
        has_pdf=bool(inv.pdf_path),
        lines=[InvoiceLineOut.model_validate(ln) for ln in inv.lines],
        finalize_blockers=finalize_blockers(inv) if inv.status == InvoiceStatus.draft else [],
        paid_amount=paid_amount,
        balance_due=balance_due,
        payment_status=payment_status,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
        created_by=_email(session, inv.created_by_user_id),
        last_edited_by=_email(session, inv.last_edited_by_user_id),
    )


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


@router.get("", response_model=list[InvoiceListItem])
def list_invoices(
    user: CurrentUser,
    session: SessionDep,
    status_: InvoiceStatus | None = Query(default=None, alias="status"),
    party_id: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    q: str | None = Query(default=None, description="party name contains"),
) -> list[InvoiceListItem]:
    # paid_amount per invoice_id in one round trip (posted allocations only)
    # so payment_status can be derived per row below without a query per
    # invoice — same aggregate-subquery approach as collections_summary.
    paid_sq = (
        select(
            PaymentAllocation.invoice_id.label("invoice_id"),
            func.sum(PaymentAllocation.amount).label("paid"),
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.type == AllocationType.against_invoice,
            Payment.status == PaymentStatus.posted,
        )
        .group_by(PaymentAllocation.invoice_id)
        .subquery()
    )

    # One WhatsApp status per invoice for the list column, in one round trip.
    # Pick the newest send; when two rows share a timestamp (a "send to two
    # numbers" batch), the more-advanced status wins so the column never
    # under-reports (failed/read > delivered > sent > pending).
    wa_rank = case(
        (WhatsappMessage.status == "failed", 4),
        (WhatsappMessage.status == "read", 3),
        (WhatsappMessage.status == "delivered", 2),
        (WhatsappMessage.status == "sent", 1),
        else_=0,
    )
    wa_rn = func.row_number().over(
        partition_by=WhatsappMessage.invoice_id,
        order_by=(
            WhatsappMessage.created_at.desc(),
            wa_rank.desc(),
            WhatsappMessage.id.desc(),
        ),
    )
    wa_ranked = (
        select(
            WhatsappMessage.invoice_id.label("invoice_id"),
            WhatsappMessage.status.label("wa_status"),
            wa_rn.label("rn"),
        )
        .where(WhatsappMessage.invoice_id.is_not(None))
        .subquery()
    )
    wa_sq = (
        select(wa_ranked.c.invoice_id, wa_ranked.c.wa_status).where(wa_ranked.c.rn == 1).subquery()
    )

    # One Tally push status per invoice for the list column (F1b-1), same
    # newest-row-wins aggregate shape as the WhatsApp column above.
    tally_rn = func.row_number().over(
        partition_by=TallySyncJob.entity_id,
        order_by=TallySyncJob.created_at.desc(),
    )
    tally_ranked = (
        select(
            TallySyncJob.entity_id.label("invoice_id"),
            TallySyncJob.status.label("job_status"),
            tally_rn.label("rn"),
        )
        .where(
            TallySyncJob.entity_type == "invoice",
            TallySyncJob.tenant_id == user.tenant_id,
        )
        .subquery()
    )
    tally_sq = (
        select(tally_ranked.c.invoice_id, tally_ranked.c.job_status)
        .where(tally_ranked.c.rn == 1)
        .subquery()
    )

    stmt = (
        select(Invoice, Party.legal_name, paid_sq.c.paid, wa_sq.c.wa_status, tally_sq.c.job_status)
        .outerjoin(Party, Party.id == Invoice.party_id)
        .outerjoin(paid_sq, paid_sq.c.invoice_id == Invoice.id)
        .outerjoin(wa_sq, wa_sq.c.invoice_id == Invoice.id)
        .outerjoin(tally_sq, tally_sq.c.invoice_id == Invoice.id)
        .where(Invoice.tenant_id == user.tenant_id)
    )
    if status_ is not None:
        stmt = stmt.where(Invoice.status == status_)
    if party_id:
        stmt = stmt.where(Invoice.party_id == party_id)
    if date_from:
        stmt = stmt.where(Invoice.date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.date <= date_to)
    if q:
        stmt = stmt.where(func.lower(Party.legal_name).like(f"%{q.lower().strip()}%"))
    stmt = stmt.order_by(
        (Invoice.status == InvoiceStatus.draft).desc(),
        Invoice.number.desc().nullsfirst(),
        Invoice.date.desc(),
        Invoice.created_at.desc(),
    )
    rows = session.execute(stmt).all()

    def _payment_status(inv: Invoice, paid: Decimal | None) -> str | None:
        if inv.status != InvoiceStatus.final:
            return None
        total = inv.grand_total or Decimal("0")
        p = Decimal(paid or 0)
        if p <= 0:
            return "unpaid"
        if p >= total:
            return "paid"
        return "partial"

    def _tally_sync_status(job_status: str | None) -> str | None:
        # Collapses the job's internal lifecycle (queued/sent/running/ok/
        # error) into the three states the UI actually distinguishes — a
        # firm doesn't need to see "sent" vs "running", just "still going".
        if job_status is None:
            return None
        if job_status == "ok":
            return "synced"
        if job_status == "error":
            return "error"
        return "pending"

    return [
        InvoiceListItem(
            id=inv.id,
            number=inv.number,
            fy=inv.fy,
            date=inv.date,
            status=inv.status,
            party_id=inv.party_id,
            party_name=name or "(no party)",
            grand_total=inv.grand_total,
            pdf_status=inv.pdf_status,
            payment_status=_payment_status(inv, paid),
            whatsapp_status=wa_status,
            tally_sync_status=_tally_sync_status(job_status),
        )
        for inv, name, paid, wa_status, job_status in rows
    ]


# --------------------------------------------------------------------------
# create / read / update / delete
# --------------------------------------------------------------------------


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceCreate, user: DraftUser, session: SessionDep) -> InvoiceOut:
    if body.party_id:
        _owned_party(session, user.tenant_id, body.party_id)
    d = body.date or date.today()
    inv = Invoice(
        tenant_id=user.tenant_id,
        doc_type=DocType.inv,
        series="Sales",
        fy=financial_year(d),
        date=d,
        party_id=body.party_id,
        bill_to_addr_id=body.bill_to_addr_id,
        ship_to_addr_id=body.ship_to_addr_id,
        notes=body.notes,
        invoice_discount=body.invoice_discount or Decimal("0"),
        weighment_slips=_slips_payload(body.weighment_slips),
        status=InvoiceStatus.draft,
        pdf_status=PdfStatus.none,
        created_by_user_id=user.id,
        last_edited_by_user_id=user.id,
    )
    _apply_lines(inv, body.lines)
    session.add(inv)
    session.flush()
    return _out(session, inv)


@router.post("/from-slip", response_model=SlipCaptureOut, status_code=status.HTTP_201_CREATED)
def create_invoice_from_slip(
    user: DraftUser,
    session: SessionDep,
    file: UploadFile = File(...),
) -> SlipCaptureOut:
    """Pilot: a photographed kachcha slip -> a draft sales invoice, prefilled
    and flagged for review. The shop already writes every sale on a slip
    before Tally entry — this reads that slip instead of asking the operator
    to re-type it. Every result always opens in the ordinary draft editor;
    nothing here is auto-accepted.
    """
    data = file.file.read()
    if len(data) > _MAX_SLIP_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That photo is over 10 MB.",
        )
    try:
        extraction = extract_slip(data)
    except SlipExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    draft = build_draft(session, user.tenant_id, extraction)

    d = date.today()
    inv = Invoice(
        tenant_id=user.tenant_id,
        doc_type=DocType.inv,
        series="Sales",
        fy=financial_year(d),
        date=d,
        party_id=draft.party_id,
        status=InvoiceStatus.draft,
        pdf_status=PdfStatus.none,
        created_by_user_id=user.id,
        last_edited_by_user_id=user.id,
    )
    _apply_lines(
        inv,
        [
            InvoiceLineIn(
                item_id=ln.item_id,
                description=ln.description,
                quantity=ln.quantity,
                uom=ln.uom,
                unit_rate=ln.unit_rate,
            )
            for ln in draft.lines
        ],
    )
    session.add(inv)
    session.flush()

    return SlipCaptureOut(
        invoice=_out(session, inv),
        line_reviews=[
            SlipLineReview(sl_no=i, needs_review=ln.needs_review, review_reason=ln.review_reason)
            for i, ln in enumerate(draft.lines, start=1)
        ],
        party_guess_name=draft.party_guess_name,
        party_needs_review=draft.party_needs_review,
        notes=draft.notes,
    )


def _resolve_transcript(session: SessionDep, tenant_id: str, transcript: str) -> VoiceLineOut:
    parsed = parse_voice_line(transcript)

    if not parsed.item_query:
        return VoiceLineOut(
            transcript=transcript,
            description="",
            needs_review=True,
            review_reason="couldn't make out an item in that — try again",
        )

    synonyms = load_synonym_map(session, tenant_id)
    match = resolve_item(session, tenant_id, parsed.item_query, synonyms=synonyms)

    needs_review = match.item_id is None or match.weak or parsed.quantity is None
    reason = None
    if parsed.quantity is None:
        reason = "quantity not understood — please check"
    elif match.item_id is None:
        reason = "no confident item match — check the item"
    elif match.weak:
        reason = "ambiguous item match — please confirm"
    elif parsed.rate is None:
        reason = "rate not spoken — check before saving"
        needs_review = True

    return VoiceLineOut(
        transcript=transcript,
        item_id=match.item_id,
        description=parsed.item_query,
        quantity=parsed.quantity,
        uom=parsed.uom,
        unit_rate=parsed.rate,
        needs_review=needs_review,
        review_reason=reason,
    )


@router.post("/voice-line", response_model=VoiceLineOut)
def resolve_voice_line(
    user: DraftUser,
    session: SessionDep,
    file: UploadFile = File(...),
) -> VoiceLineOut:
    """Pilot: one push-to-talk recording -> one resolved invoice line.

    Resolve-only — no invoice side effects. The editor calls this per line
    while the operator is building a draft and appends the result to its own
    row state, exactly as a typed line is added; nothing here auto-commits.
    """
    data = file.file.read()
    keyterms = top_item_keyterms(session, user.tenant_id)
    try:
        tx = transcribe(data, file.content_type or "audio/webm", keyterms=keyterms)
    except TranscriptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return _resolve_transcript(session, user.tenant_id, tx.text)


@router.post("/voice-line/resolve-text", response_model=VoiceLineOut)
def resolve_voice_line_transcript(
    user: DraftUser,
    session: SessionDep,
    body: VoiceLineTranscriptIn,
) -> VoiceLineOut:
    """Real-time pilot: the accumulated transcript from the streaming
    WebSocket (app/routers/voice_stream.py) -> one resolved line. No audio
    here — Deepgram already ran during the live stream; this just does the
    same qty/uom/rate/item parse `/voice-line` does from text already in
    hand, so the parser stays in one place."""
    return _resolve_transcript(session, user.tenant_id, body.transcript)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: str, user: CurrentUser, session: SessionDep) -> InvoiceOut:
    return _out(session, _load(session, user.tenant_id, invoice_id))


@router.get(
    "/{invoice_id}/whatsapp",
    response_model=list[InvoiceWhatsappMessageOut],
    tags=["whatsapp"],
)
def list_invoice_whatsapp(
    invoice_id: str, user: CurrentUser, session: SessionDep
) -> list[WhatsappMessage]:
    """Every WhatsApp send attempt for this invoice, newest first. Statuses
    move on their own as Meta delivery/read/failed webhooks arrive."""
    _load(session, user.tenant_id, invoice_id)  # 404s if not the caller's
    return list(
        session.scalars(
            select(WhatsappMessage)
            .where(WhatsappMessage.invoice_id == invoice_id)
            .order_by(WhatsappMessage.created_at.desc(), WhatsappMessage.id.desc())
        )
    )


@router.put("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    invoice_id: str, body: InvoiceUpdate, user: DraftUser, session: SessionDep
) -> InvoiceOut:
    inv = _load(session, user.tenant_id, invoice_id, lock=True)
    if inv.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"invoice is {inv.status} and cannot be edited",
        )
    _check_not_changed_by_someone_else(session, inv, body.expected_updated_at, user)

    if body.party_id is not None and body.party_id != (inv.party_id or ""):
        if body.party_id:
            _owned_party(session, user.tenant_id, body.party_id)
            inv.party_id = body.party_id
        else:
            inv.party_id = None
    if body.date is not None:
        inv.date = body.date
        inv.fy = financial_year(body.date)
    if body.bill_to_addr_id is not None:
        inv.bill_to_addr_id = body.bill_to_addr_id or None
    if body.ship_to_addr_id is not None:
        inv.ship_to_addr_id = body.ship_to_addr_id or None
    if body.notes is not None:
        inv.notes = body.notes
    if body.invoice_discount is not None:
        inv.invoice_discount = body.invoice_discount
    if body.lines is not None:
        _apply_lines(inv, body.lines)
    if body.weighment_slips is not None:
        inv.weighment_slips = _slips_payload(body.weighment_slips)

    # an edit that only changed lines would not touch the invoice row, so stamp it ourselves:
    # this is the value the next editor must present to prove it saw this version
    inv.updated_at = datetime.now(UTC)
    inv.last_edited_by_user_id = user.id
    session.flush()
    return _out(session, inv)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: str, user: DraftUser, session: SessionDep) -> None:
    inv = _load(session, user.tenant_id, invoice_id)
    can_remove_cancelled = user.role in (UserRole.owner, UserRole.accountant)
    if inv.status == InvoiceStatus.cancelled and not can_remove_cancelled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner or accountant can remove a cancelled invoice",
        )
    if inv.status == InvoiceStatus.final:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a finalized invoice cannot be deleted — cancel it instead",
        )
    # A *posted* payment blocks delete — reverse it first (real money was
    # recorded; deleting the invoice out from under it would be silently
    # discarding that fact). A *reversed* payment does NOT block: its
    # allocation row stays forever for the audit trail (never deleted), but
    # payment_allocation.invoice_id is ON DELETE SET NULL (migration 0019)
    # specifically so this delete can still go through — the payment record
    # itself, reversed status included, is untouched either way.
    has_posted_payment = session.scalar(
        select(PaymentAllocation.id)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == inv.id, Payment.status == PaymentStatus.posted)
        .limit(1)
    )
    if has_posted_payment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot delete — a payment is recorded against this invoice; reverse it first",
        )
    # an order that made this draft goes back to being an open order
    session.execute(
        update(CustomerOrder)
        .where(CustomerOrder.invoice_id == inv.id)
        .values(invoice_id=None, status="accepted")
    )
    # a draft (never numbered) or a cancelled invoice (number already burned,
    # not reused) can be removed outright
    session.delete(inv)


# --------------------------------------------------------------------------
# finalize / cancel / duplicate
# --------------------------------------------------------------------------


@router.post("/{invoice_id}/finalize", response_model=FinalizeOut)
def finalize(
    invoice_id: str, background: BackgroundTasks, user: WriteUser, session: SessionDep
) -> FinalizeOut:
    inv = _load(session, user.tenant_id, invoice_id)
    try:
        result = finalize_invoice(session, inv, actor_user_id=user.id)
    except FinalizeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reasons
        ) from exc
    # the order this invoice came from is now invoiced, and its customer is told
    order_ids = list(
        session.scalars(select(CustomerOrder.id).where(CustomerOrder.invoice_id == inv.id))
    )
    session.execute(
        update(CustomerOrder).where(CustomerOrder.invoice_id == inv.id).values(status="invoiced")
    )
    session.flush()
    if order_ids:
        session.commit()  # the background task reads these from its own session
        for oid in order_ids:
            background.add_task(order_notify.notify, oid, "invoiced")
    return FinalizeOut(
        id=inv.id,
        number=result.number,
        fy=result.fy,
        status=inv.status,
        totals=totals_for(inv),
        measure=measure_for(inv),
        pdf_status=result.pdf_status,
        created_item_ids=result.created_item_ids,
        learned_group_ids=result.learned_group_ids,
    )


@router.post("/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(invoice_id: str, user: WriteUser, session: SessionDep) -> InvoiceOut:
    inv = _load(session, user.tenant_id, invoice_id)
    if inv.status == InvoiceStatus.cancelled:
        return _out(session, inv)
    if inv.status == InvoiceStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a draft has no number to cancel — delete it instead",
        )
    # A finalized invoice is permanent: its number is issued and it may already
    # be in the customer's hands / a filed return. It cannot be cancelled or
    # deleted — correct it with a credit note or a fresh invoice instead.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="a finalized invoice cannot be cancelled — issue a credit note instead",
    )


@router.post("/{invoice_id}/duplicate", response_model=DuplicateOut, status_code=201)
def duplicate_invoice(invoice_id: str, user: DraftUser, session: SessionDep) -> DuplicateOut:
    src = _load(session, user.tenant_id, invoice_id)
    d = date.today()
    clone = Invoice(
        tenant_id=user.tenant_id,
        doc_type=DocType.inv,
        series="Sales",
        fy=financial_year(d),
        date=d,
        party_id=src.party_id,
        bill_to_addr_id=src.bill_to_addr_id,
        ship_to_addr_id=src.ship_to_addr_id,
        notes=src.notes,
        invoice_discount=src.invoice_discount or Decimal("0"),
        weighment_slips=list(src.weighment_slips) if src.weighment_slips else None,
        status=InvoiceStatus.draft,
        pdf_status=PdfStatus.none,
        created_by_user_id=user.id,
        last_edited_by_user_id=user.id,
    )
    for ln in sorted(src.lines, key=lambda x: x.sl_no):
        clone.lines.append(
            InvoiceLine(
                sl_no=ln.sl_no,
                item_id=ln.item_id,
                description=ln.description,
                hsn_code=ln.hsn_code,
                quantity=ln.quantity,
                uom=ln.uom,
                unit_rate=ln.unit_rate,
                discount=ln.discount,
                discount_pct=ln.discount_pct,
                size_pos=ln.size_pos,
                segment_no=ln.segment_no or 1,
            )
        )
    session.add(clone)
    session.flush()
    return DuplicateOut(id=clone.id)


# --------------------------------------------------------------------------
# pdf
# --------------------------------------------------------------------------


@router.get("/{invoice_id}/pdf")
def get_pdf(invoice_id: str, user: CurrentUser, session: SessionDep) -> FileResponse:
    inv = _load(session, user.tenant_id, invoice_id)
    from pathlib import Path

    if not inv.pdf_path or not Path(inv.pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF not available — try re-render")
    return FileResponse(inv.pdf_path, media_type="application/pdf", filename=download_name(inv))


@router.post("/{invoice_id}/rerender", response_model=InvoiceOut)
def rerender_pdf(invoice_id: str, user: WriteUser, session: SessionDep) -> InvoiceOut:
    inv = _load(session, user.tenant_id, invoice_id)
    if inv.status != InvoiceStatus.final:
        raise HTTPException(status_code=409, detail="only a finalized invoice has a PDF")
    from app.services.invoices.pdf import render_invoice_pdf

    render_invoice_pdf(session, inv)
    session.flush()
    return _out(session, inv)

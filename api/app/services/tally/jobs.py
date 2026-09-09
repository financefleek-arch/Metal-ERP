"""Tally Connector sync-job lifecycle + the "one job in flight" guards.

A `tally_sync_job` of kind `pull_masters` (F1a) is created alongside an
`AgentOutboxItem` the companion agent drains on its next checkin. The agent
reads the shop's Tally export folder, uploads the XML to R2, and calls
`POST /api/tally-agent/jobs/{id}/result`, which stages a batch and marks the
job `ok` / `error`.

`assert_no_pull_in_flight` is shared by `POST /api/tally/pull-masters` and
the two manual file-upload routes so the three can't stomp each other's
staging batch (the staging tables allow only one un-committed batch per
tenant — a policy, not a schema rule).

F1b-1 adds `kind='push_sales'`, `direction='out'` jobs — one per invoice
push attempt, keyed by `(entity_type='invoice', entity_id)`. Same outbox/
checkin/agent-status-ping machinery, generalized `_expire_stuck_job` (was
pull-only) for the "wedged in Tally-unavailable" auto-cancel.

F5d adds `kind='push_purchase'` jobs, keyed by
`(entity_type='inward_bill', entity_id)` — same transport, same
`TallyLink`/`TallySyncJob` shape (already entity-generic from F1b-1's own
`0027` migration), just a different serializer at enqueue time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentOutboxItem,
    Invoice,
    InwardBill,
    Party,
    StagingTallyItem,
    StagingTallyParty,
    TallyCompany,
    TallyLink,
    TallySyncJob,
)

# job.status values that mean "the agent still owes us a result"
_NON_TERMINAL = ("queued", "sent", "running")

# A job that has been `sent` this long with only "not ready" agent pings
# (Tally closed / no company open) and no real result is auto-cancelled on
# the next attempt, so a mis-timed job can't wedge the tenant forever. Same
# window for pull and push in this slice — see the F1b-1 plan's Open
# Question #3 on whether push should differ.
_STUCK_AFTER = timedelta(minutes=10)


def _expire_stuck_job(session: Session, job: TallySyncJob) -> bool:
    """Lazy auto-cancel: if `job` is `sent`, older than the window, and its
    agent only ever reported a 'not ready' status, move it to `error` and
    return True (the caller may then proceed with a fresh job). Works for
    any `kind` — pull or push.
    """
    if job.status != "sent":
        return False
    age = datetime.now(UTC) - _as_utc(job.created_at)
    if age < _STUCK_AFTER:
        return False
    # only expire if the agent actually saw it and kept saying "not ready"
    if job.last_agent_status not in ("no_company_loaded", "tally_unavailable"):
        return False
    job.status = "error"
    job.error = "Tally stayed unavailable — retried, then cancelled."
    job.completed_at = datetime.now(UTC)
    session.flush()
    return True


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def assert_no_pull_in_flight(session: Session, tenant_id: str) -> None:
    """409 if this tenant has a masters pull that is either still running or
    finished-ok with staged rows the operator hasn't committed or discarded
    yet. Keeps a second pull — or a manual file upload — from wiping the
    un-reviewed batch.
    """
    running = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.tenant_id == tenant_id,
            TallySyncJob.kind == "pull_masters",
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if running is not None and not _expire_stuck_job(session, running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A Tally masters pull is already in progress. Wait for it to "
                "finish before starting another."
            ),
        )

    uncommitted = session.scalar(
        select(StagingTallyParty.id).where(
            StagingTallyParty.tenant_id == tenant_id,
            StagingTallyParty.committed_as.is_(None),
            StagingTallyParty.sync_job_id.is_not(None),
        )
    ) or session.scalar(
        select(StagingTallyItem.id).where(
            StagingTallyItem.tenant_id == tenant_id,
            StagingTallyItem.committed_as.is_(None),
            StagingTallyItem.sync_job_id.is_not(None),
        )
    )
    if uncommitted is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A Tally masters pull is waiting for review. Commit or discard "
                "that batch before starting another."
            ),
        )


def enqueue_pull_masters(
    session: Session, company: TallyCompany
) -> TallySyncJob:
    """Create the job + the paired agent outbox item. The caller has already
    run `assert_no_pull_in_flight`.
    """
    if company.shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Link a companion-agent install to this Tally company first.",
        )

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="in",
        kind="pull_masters",
        status="queued",
    )
    session.add(job)
    session.flush()

    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": "pull_masters",
            "company_name": company.company_name,
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()

    job.outbox_item_id = outbox.id
    session.flush()
    return job


def mark_job_sent(session: Session, job: TallySyncJob) -> None:
    """The agent picked the outbox item up (seen when checkin flips the
    outbox row to `sent`). Best-effort — not every transport reports this.
    """
    if job.status == "queued":
        job.status = "sent"
        session.flush()


def record_agent_status(
    session: Session, job: TallySyncJob, agent_status: str
) -> None:
    """The agent pinged a 'not ready' reason (Tally closed / no company
    open) instead of a real result — record it for the Pull step indicator
    and the lazy auto-cancel. Only meaningful while the job is non-terminal.
    """
    if job.status in ("ok", "error"):
        return
    job.last_agent_status = agent_status[:32]
    job.last_agent_status_at = datetime.now(UTC)
    if job.status == "queued":
        job.status = "sent"
    session.flush()


def complete_job_ok(
    session: Session, job: TallySyncJob, *, batch_id: str, counts: dict
) -> None:
    job.status = "ok"
    job.batch_id = batch_id
    job.counts = counts
    job.completed_at = datetime.now(UTC)
    session.flush()


def complete_job_error(session: Session, job: TallySyncJob, *, error: str) -> None:
    job.status = "error"
    job.error = error[:2000]
    job.completed_at = datetime.now(UTC)
    session.flush()


# --------------------------------------------------------------------------
# F1b-1 — push_sales
# --------------------------------------------------------------------------


def invoice_checksum(invoice: Invoice) -> str:
    """A cheap fingerprint of the frozen totals — good enough to detect
    "this invoice hasn't changed since it was last pushed" without hashing
    every line. Finalized invoices are immutable, so this is defensive
    (a re-push attempt on an unchanged invoice), not a real mutation guard.
    Shared with `services/tally/push.py`, which computes the same
    fingerprint after a successful push to store on the `tally_link`.
    """
    lines = [ln for ln in invoice.lines if (ln.description or "").strip()]
    return f"{invoice.grand_total}|{len(lines)}|{sum((ln.line_total or 0) for ln in lines)}"


def assert_no_push_in_flight(session: Session, tenant_id: str, invoice_id: str) -> None:
    """409 if this invoice already has a non-terminal push_sales job."""
    _assert_no_push_in_flight(
        session,
        tenant_id=tenant_id,
        kind="push_sales",
        entity_type="invoice",
        entity_id=invoice_id,
        noun="invoice",
    )


def assert_no_purchase_push_in_flight(session: Session, tenant_id: str, bill_id: str) -> None:
    """409 if this inward bill already has a non-terminal push_purchase job."""
    _assert_no_push_in_flight(
        session,
        tenant_id=tenant_id,
        kind="push_purchase",
        entity_type="inward_bill",
        entity_id=bill_id,
        noun="bill",
    )


def _assert_no_push_in_flight(
    session: Session, *, tenant_id: str, kind: str, entity_type: str, entity_id: str, noun: str
) -> None:
    running = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.tenant_id == tenant_id,
            TallySyncJob.kind == kind,
            TallySyncJob.entity_type == entity_type,
            TallySyncJob.entity_id == entity_id,
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if running is not None and not _expire_stuck_job(session, running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A Tally push for this {noun} is already in progress. Wait "
                "for it to finish before trying again."
            ),
        )


def enqueue_push_sales(session: Session, company: TallyCompany, invoice: Invoice) -> TallySyncJob:
    """Create the job + paired outbox item — including the built voucher
    XML, stashed directly in `AgentOutboxItem.payload["voucher_xml"]`
    (matches F1a's own pattern of putting job parameters in the outbox
    payload; a voucher is only a few KB, so no R2 round-trip is needed the
    way pull's masters XML needs one). The caller has already run
    `push_readiness.push_blockers()` (empty) and `assert_no_push_in_flight`
    — those checks are what guarantee `build_sales_voucher_xml_bytes`
    below won't raise.

    Idempotent: if `invoice` already has a `tally_link` whose `checksum`
    matches its current totals, no job is created — the existing link is
    returned wrapped in a synthetic already-`ok` job-shaped result instead.
    Since finalized invoices are immutable this mainly guards a defensive
    re-enqueue (e.g. a double-click), not a real content change.
    """
    from app.services.invoices.tally_xml import build_sales_voucher_xml_bytes
    checksum = invoice_checksum(invoice)
    existing_link = session.scalar(
        select(TallyLink).where(
            TallyLink.tenant_id == company.tenant_id,
            TallyLink.entity_type == "invoice",
            TallyLink.entity_id == invoice.id,
        )
    )
    if existing_link is not None and existing_link.checksum == checksum:
        # Already pushed, unchanged — return the most recent job for it
        # rather than creating a new no-op job.
        prior = session.scalar(
            select(TallySyncJob)
            .where(
                TallySyncJob.tenant_id == company.tenant_id,
                TallySyncJob.kind == "push_sales",
                TallySyncJob.entity_type == "invoice",
                TallySyncJob.entity_id == invoice.id,
                TallySyncJob.status == "ok",
            )
            .order_by(TallySyncJob.completed_at.desc())
        )
        if prior is not None:
            return prior

    party = session.get(Party, invoice.party_id) if invoice.party_id else None
    if party is None:
        # push_blockers() should have caught this already (party_not_linked
        # implies a party exists); defensive, not expected to be reached.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invoice has no party.",
        )
    voucher_xml = build_sales_voucher_xml_bytes(invoice, party, company.ledger_map or {})

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="out",
        kind="push_sales",
        entity_type="invoice",
        entity_id=invoice.id,
        status="queued",
    )
    session.add(job)
    session.flush()

    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": "push_sales",
            "company_name": company.company_name,
            # decoded to text for the JSON payload column — the agent
            # re-encodes as UTF-8 bytes before POSTing (see Finding 3: the
            # live HTTP path needs UTF-8, not the purchase side's UTF-16).
            "voucher_xml": voucher_xml.decode("utf-8"),
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()

    job.outbox_item_id = outbox.id
    session.flush()
    return job


def complete_push_ok(
    session: Session,
    job: TallySyncJob,
    *,
    tally_guid: str,
    checksum: str,
) -> TallyLink:
    """Write/update the `tally_link` row for the pushed entity, mark the
    job `ok`. `tally_guid` is the Tally VOUCHERKEY if the response carried
    one, else a deterministic fallback (see `services/tally/push.py`).
    `entity_type` is read off the job itself (`invoice` for push_sales,
    `inward_bill` for push_purchase) — both kinds share this one function.
    """
    entity_type = job.entity_type or "invoice"
    link = session.scalar(
        select(TallyLink).where(
            TallyLink.tenant_id == job.tenant_id,
            TallyLink.entity_type == entity_type,
            TallyLink.entity_id == job.entity_id,
        )
    )
    now = datetime.now(UTC)
    if link is None:
        link = TallyLink(
            tenant_id=job.tenant_id,
            entity_type=entity_type,
            entity_id=job.entity_id,
            tally_guid=tally_guid,
            checksum=checksum,
            last_pushed_at=now,
        )
        session.add(link)
    else:
        link.tally_guid = tally_guid
        link.checksum = checksum
        link.last_pushed_at = now

    job.status = "ok"
    job.completed_at = now
    session.flush()
    return link


# --------------------------------------------------------------------------
# F5d — push_purchase
# --------------------------------------------------------------------------


def bill_checksum(bill: InwardBill) -> str:
    """Same idea as `invoice_checksum` — a cheap fingerprint of the frozen
    totals, good enough to detect "this bill hasn't changed since it was
    last pushed" for idempotency. An approved bill's totals are frozen the
    same way a finalized invoice's are.
    """
    lines = list(bill.lines)
    return f"{bill.grand_total}|{len(lines)}|{sum((ln.line_total or 0) for ln in lines)}"


def enqueue_push_purchase(
    session: Session, company: TallyCompany, bill: InwardBill
) -> TallySyncJob:
    """Create the job + paired outbox item for a Purchase-voucher push —
    mirrors `enqueue_push_sales` exactly (see its docstring for the shared
    shape: XML built at enqueue time and stashed in the outbox payload,
    idempotent on an unchanged already-linked bill). The caller has
    already run `push_readiness.purchase_push_blockers()` (empty) and
    `assert_no_purchase_push_in_flight`.
    """
    from app.services.inward.tally_xml import LedgerConfig, build_xml_bytes

    checksum = bill_checksum(bill)
    existing_link = session.scalar(
        select(TallyLink).where(
            TallyLink.tenant_id == company.tenant_id,
            TallyLink.entity_type == "inward_bill",
            TallyLink.entity_id == bill.id,
        )
    )
    if existing_link is not None and existing_link.checksum == checksum:
        prior = session.scalar(
            select(TallySyncJob)
            .where(
                TallySyncJob.tenant_id == company.tenant_id,
                TallySyncJob.kind == "push_purchase",
                TallySyncJob.entity_type == "inward_bill",
                TallySyncJob.entity_id == bill.id,
                TallySyncJob.status == "ok",
            )
            .order_by(TallySyncJob.completed_at.desc())
        )
        if prior is not None:
            return prior

    party = session.get(Party, bill.matched_party_id) if bill.matched_party_id else None
    if party is None:
        # purchase_push_blockers() should have caught this already
        # (party_not_linked); defensive, not expected to be reached.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bill has no linked supplier.",
        )

    ledger_map = company.ledger_map or {}
    cfg = LedgerConfig(
        creditors_group=ledger_map.get("creditors_parent") or "Sundry Creditors",
        purchase_ledger=ledger_map.get("purchase_ledger") or "Purchase Accounts",
        cgst_ledger=ledger_map.get("cgst_ledger") or "CGST",
        sgst_ledger=ledger_map.get("sgst_ledger") or "SGST",
        igst_ledger=ledger_map.get("igst_ledger") or "IGST",
        round_off_ledger=ledger_map.get("round_off_ledger") or "Round Off",
        # Live push over HTTP needs UTF-8 — the file-export path (still
        # used by approve.py for the manual-import safety net) keeps its
        # own UTF-16-default LedgerConfig separately; this one is built
        # fresh, only for the live-push call.
        xml_encoding="UTF-8",
    )
    voucher_xml = build_xml_bytes(bill, cfg, party_name=party.legal_name)

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="out",
        kind="push_purchase",
        entity_type="inward_bill",
        entity_id=bill.id,
        status="queued",
    )
    session.add(job)
    session.flush()

    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": "push_purchase",
            "company_name": company.company_name,
            "voucher_xml": voucher_xml.decode("utf-8"),
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()

    job.outbox_item_id = outbox.id
    session.flush()
    return job

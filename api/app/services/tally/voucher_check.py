"""Did a voucher we pushed already land in Tally? (S0.6, gap F1)

Tally does not reject a duplicate: posting the same voucher twice creates two (verified live,
2026-10-30). So when a push's outcome is unknown (the agent never reported back, or failed after it had
sent the voucher), a retry must not simply post again. It first asks Tally, through the same read-only
export the agent already runs for voucher capture (so no new agent release), whether a voucher with our
reference exists:

  - sales: `ME/<fy>/<number>` in the Reference field (`invoices.tally_xml.sales_reference`);
  - purchase: the supplier's bill number, which that voucher already carries as its Reference.

A user-defined field cannot be read back, which is why the standard Reference field is used.

Found: the earlier push is completed as ok and linked (nothing is posted). Not found: the retry goes
ahead. The check itself failing (agent down, Tally refused the company) leaves the retry held, because
posting blind is exactly what this exists to prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from xml.sax.saxutils import escape

from fastapi import HTTPException, status
from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backup_storage import R2NotConfigured, get_object
from app.models import AgentOutboxItem, Invoice, InwardBill, TallyCompany, TallySyncJob
from app.services.invoices.tally_xml import sales_reference
from app.services.tally.company_identity import company_payload
from app.services.tally.jobs import (
    _NON_TERMINAL,
    _expire_stuck_job,
    bill_checksum,
    complete_job_error,
    complete_push_ok,
    invoice_checksum,
)
from app.services.tally.voucher_capture import is_export_request

log = logging.getLogger("tally.voucher_check")

KIND = "check_voucher"
_ENTITY_KIND = {"invoice": "push_sales", "inward_bill": "push_purchase"}

_REQUEST = (
    "<ENVELOPE>"
    "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
    "<TYPE>Collection</TYPE><ID>MERefCheck</ID></HEADER>"
    "<BODY><DESC><STATICVARIABLES>"
    "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    "<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>"
    "<SVFROMDATE>{day}</SVFROMDATE><SVTODATE>{day}</SVTODATE>"
    "</STATICVARIABLES>"
    "<TDL><TDLMESSAGE>"
    '<COLLECTION NAME="MERefCheck" ISMODIFY="No"><TYPE>Voucher</TYPE><FILTER>MERefFilter</FILTER>'
    "<FETCH>Date, VoucherTypeName, VoucherNumber, Reference, PartyLedgerName, GUID, MasterID</FETCH>"
    "</COLLECTION>"
    '<SYSTEM TYPE="Formulae" NAME="MERefFilter">$Reference = "{reference}"</SYSTEM>'
    "</TDLMESSAGE></TDL>"
    "</DESC></BODY></ENVELOPE>"
)


class CheckFailed(ValueError):
    """Tally's answer could not be read as a list of vouchers (it refused, or sent an error)."""


@dataclass(frozen=True)
class FoundVoucher:
    guid: str
    master_id: str
    number: str
    voucher_type: str
    party: str


def build_reference_request(company_name: str, on_date: date, reference: str) -> str:
    # The reference sits inside a TDL formula string, so a double quote would end it.
    if '"' in reference:
        raise ValueError("a reference with a double quote cannot be looked up")
    return _REQUEST.format(
        company=escape(company_name), day=on_date.strftime("%Y%m%d"), reference=escape(reference)
    )


def _text(el: etree._Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def parse_reference_result(
    raw: bytes, reference: str, *, voucher_type: str, party: str | None = None
) -> list[FoundVoucher]:
    """Vouchers in Tally's answer whose Reference is exactly `reference` (and, when given, of that
    voucher type and party). Raises `CheckFailed` if the answer is an error."""
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError as e:
        raise CheckFailed(f"could not read Tally's answer: {e}") from e
    err = root.find(".//LINEERROR")
    if err is not None and (err.text or "").strip():
        raise CheckFailed(err.text.strip())
    if (root.findtext(".//HEADER/STATUS") or "").strip() != "1":
        raise CheckFailed("Tally did not answer the lookup")

    found: list[FoundVoucher] = []
    for v in root.iter("VOUCHER"):
        if _text(v, "REFERENCE") != reference:
            continue
        if _text(v, "VOUCHERTYPENAME").lower() != voucher_type.lower():
            continue
        name = _text(v, "PARTYLEDGERNAME")
        if party and name.lower() != party.strip().lower():
            continue
        found.append(
            FoundVoucher(
                guid=_text(v, "GUID"),
                master_id=_text(v, "MASTERID"),
                number=_text(v, "VOUCHERNUMBER"),
                voucher_type=_text(v, "VOUCHERTYPENAME"),
                party=name,
            )
        )
    return found


# --------------------------------------------------------------------------
# the gate in front of a retry
# --------------------------------------------------------------------------


def _latest_unknown_push(session: Session, tenant_id: str, entity_type: str, entity_id: str):
    return session.scalar(
        select(TallySyncJob)
        .where(
            TallySyncJob.tenant_id == tenant_id,
            TallySyncJob.kind == _ENTITY_KIND[entity_type],
            TallySyncJob.entity_type == entity_type,
            TallySyncJob.entity_id == entity_id,
            TallySyncJob.outcome_unknown.is_(True),
        )
        .order_by(TallySyncJob.created_at.desc())
    )


def _lookup(entity: Invoice | InwardBill) -> tuple[str, date, str, str] | None:
    """(entity_type, voucher date, reference, voucher type) or None when the voucher carries nothing
    to look it up by (a purchase with no bill number)."""
    if isinstance(entity, Invoice):
        return "invoice", entity.date, sales_reference(entity), "Sales"
    ref = (entity.bill_no or "").strip()
    if not ref or entity.bill_date is None:
        return None
    return "inward_bill", entity.bill_date, ref, "Purchase"


def pending_check(
    session: Session, company: TallyCompany, entity: Invoice | InwardBill
) -> TallySyncJob | None:
    """The check job to hand back instead of posting again, or None when the retry may go ahead.

    A retry is held only when an earlier push of this same document ended with an unknown outcome and
    has not been checked yet. A check already in flight is returned as is, so a double click does not
    queue two.
    """
    look = _lookup(entity)
    if look is None:
        return None
    entity_type, on_date, reference, voucher_type = look

    running = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.tenant_id == company.tenant_id,
            TallySyncJob.kind == KIND,
            TallySyncJob.entity_type == entity_type,
            TallySyncJob.entity_id == entity.id,
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if running is not None and not _expire_stuck_job(session, running):
        return running

    if _latest_unknown_push(session, company.tenant_id, entity_type, entity.id) is None:
        return None
    return enqueue_voucher_check(
        session,
        company,
        entity_type=entity_type,
        entity_id=entity.id,
        on_date=on_date,
        reference=reference,
        voucher_type=voucher_type,
    )


def enqueue_voucher_check(
    session: Session,
    company: TallyCompany,
    *,
    entity_type: str,
    entity_id: str,
    on_date: date,
    reference: str,
    voucher_type: str,
) -> TallySyncJob:
    if company.shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Link a companion-agent install to this Tally company first.",
        )
    request_xml = build_reference_request(company.company_name, on_date, reference)
    if not is_export_request(request_xml):  # the agent refuses anything else
        raise ValueError("a voucher check must be a read-only Export request")

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="in",
        kind=KIND,
        entity_type=entity_type,
        entity_id=entity_id,
        status="queued",
        counts={"reference": reference, "voucher_type": voucher_type},
    )
    session.add(job)
    session.flush()
    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            # the agent already runs read-only exports under this action (voucher capture)
            "action": "pull_vouchers",
            **company_payload(company),
            "label": "check",
            "export_xml": request_xml,
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()
    job.outbox_item_id = outbox.id
    session.flush()
    return job


# --------------------------------------------------------------------------
# the agent's answer
# --------------------------------------------------------------------------


def _finish(job: TallySyncJob, counts: dict) -> None:
    job.counts = {**(job.counts or {}), **counts}
    job.status = "ok"
    job.completed_at = datetime.now(UTC)


def process_check_result(
    session: Session, job: TallySyncJob, *, ok: bool, r2_key: str | None, agent_error: str | None
) -> None:
    """Idempotent, and never raises back at the agent."""
    if job.status in ("ok", "error"):
        return
    if not ok:
        complete_job_error(session, job, error=agent_error or "agent reported a failure")
        return
    if not r2_key:
        complete_job_error(session, job, error="agent result missing r2_key")
        return

    job.r2_key = r2_key
    session.flush()
    try:
        raw = get_object(r2_key)
    except R2NotConfigured as e:
        complete_job_error(session, job, error=f"cloud storage not configured: {e}")
        return
    except Exception as e:  # noqa: BLE001 - any download failure becomes a job error
        log.exception("voucher check %s: R2 download failed", job.id)
        complete_job_error(session, job, error=f"could not download Tally's answer: {e}")
        return

    counts = job.counts or {}
    reference = str(counts.get("reference") or "")
    voucher_type = str(counts.get("voucher_type") or "")
    try:
        found = parse_reference_result(raw, reference, voucher_type=voucher_type)
    except CheckFailed as e:
        # Held on purpose: the earlier push stays flagged unknown, so the next retry checks again.
        complete_job_error(
            session,
            job,
            error=(
                f"Could not check Tally for the earlier push ({e}). Nothing was posted again; "
                "open Tally to see whether the voucher is there, then retry."
            ),
        )
        return

    entity_type = job.entity_type or "invoice"
    prior = _latest_unknown_push(session, job.tenant_id, entity_type, job.entity_id or "")
    company = session.get(TallyCompany, job.company_id)

    if found:
        v = found[0]
        if prior is not None:
            entity = (
                session.get(Invoice, job.entity_id)
                if entity_type == "invoice"
                else session.get(InwardBill, job.entity_id)
            )
            checksum = (
                invoice_checksum(entity) if entity_type == "invoice" else bill_checksum(entity)
            )
            complete_push_ok(session, prior, tally_guid=v.guid or v.master_id, checksum=checksum)
            prior.outcome_unknown = False
            prior.error = None
        _finish(
            job,
            {
                "found": True,
                "voucher_number": v.number,
                "master_id": v.master_id,
                "copies": len(found),
            },
        )
        session.flush()
        return

    # Not there: the earlier push never landed, so the retry the user asked for may now go ahead.
    if prior is not None:
        prior.outcome_unknown = False
    _finish(job, {"found": False})
    session.flush()
    _resume_push(session, job, company, entity_type)


def _resume_push(
    session: Session, job: TallySyncJob, company: TallyCompany | None, entity_type: str
) -> None:
    from app.services.tally.jobs import enqueue_push_purchase, enqueue_push_sales
    from app.services.tally.push_readiness import (
        is_pushable,
        purchase_push_blockers,
        push_blockers,
    )

    if company is None:
        return
    try:
        if entity_type == "invoice":
            invoice = session.get(Invoice, job.entity_id)
            if invoice is None or not is_pushable(push_blockers(session, invoice)):
                job.counts = {**(job.counts or {}), "resume": "blocked"}
                return
            enqueue_push_sales(session, company, invoice, checked=True)
        else:
            bill = session.get(InwardBill, job.entity_id)
            if bill is None or not is_pushable(purchase_push_blockers(session, bill)):
                job.counts = {**(job.counts or {}), "resume": "blocked"}
                return
            enqueue_push_purchase(session, company, bill, checked=True)
        job.counts = {**(job.counts or {}), "resume": "queued"}
    except HTTPException as e:
        job.counts = {**(job.counts or {}), "resume": f"not queued: {e.detail}"}

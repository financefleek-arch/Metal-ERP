"""Process a completed `push_sales` / `push_purchase` job: parse Tally's
Import-Data response, write/update the `tally_link`, mark the job.

Called from the agent-facing result callback
(`POST /api/tally-agent/jobs/{id}/result`). Unlike `pull.py`, there is no
R2 round-trip here — the voucher XML the agent posts to Tally is small (a
few KB), and Tally's response is sent back to us directly in the result
callback body (`tally_response`), not uploaded anywhere. All failures are
turned into a job `error` — this path must never 500 back at the agent.

The response-parsing helpers (`<CREATED>`/`<LINEERROR>`/voucher-key
extraction) are voucher-type-agnostic — Tally's Import-Data response shape
doesn't differ between a Sales and a Purchase voucher. `process_push_result`
dispatches on `job.kind` only to pick the right entity table + checksum
function (F5d).
"""

from __future__ import annotations

import logging

from lxml import etree
from sqlalchemy.orm import Session

from app.models import Invoice, InwardBill, TallyCompany, TallySyncJob
from app.services.tally.company_identity import HARD_STOPS
from app.services.tally.jobs import (
    bill_checksum,
    complete_job_error,
    complete_push_ok,
    invoice_checksum,
)

log = logging.getLogger("tally.push")


def _extract_created_count(root: etree._Element) -> int:
    el = root.find(".//CREATED")
    if el is None or not (el.text or "").strip():
        return 0
    try:
        return int(el.text.strip())
    except ValueError:
        return 0


def _extract_line_error(root: etree._Element) -> str | None:
    el = root.find(".//LINEERROR")
    if el is not None and (el.text or "").strip():
        return el.text.strip()
    return None


def _extract_voucher_key(root: etree._Element) -> str | None:
    """Tally's Import-Data response does not reliably carry a VOUCHERKEY
    (unresolved as of the F1b-1 live probe — see the plan's Open Question
    #1). Look for it defensively under a couple of plausible tag names;
    the caller falls back to a deterministic value if none is found.
    """
    for tag in ("VOUCHERKEY", "MASTERID", "LASTVCHID"):
        el = root.find(f".//{tag}")
        if el is not None and (el.text or "").strip():
            return el.text.strip()
    return None


def voucher_guid(company_guid: str | None, master_id: str | None) -> str | None:
    """A voucher's GUID as Tally writes it: the company's GUID, a dash and the voucher's master id as 8
    hex digits (live-verified, S0.1). None when either part is unknown."""
    cg = (company_guid or "").strip()
    mid = (master_id or "").strip()
    if not cg or not mid.isdigit():
        return None
    return f"{cg}-{int(mid):08x}"


def voucher_identity(
    root: etree._Element, company_guid: str | None
) -> tuple[str | None, str | None]:
    """(key to store as the link's `tally_guid`, voucher master id) from an Import response (S4.1).

    `LASTVCHID` is the voucher's master id. With the company's GUID known the key is the real voucher
    GUID; without it the master id itself, which still identifies the voucher inside that company.
    `(None, None)` when the response carries neither, and the caller then marks the link unconfirmed.
    """
    el = root.find(".//LASTVCHID")
    master_id = (el.text or "").strip() if el is not None else ""
    if master_id.isdigit():
        return voucher_guid(company_guid, master_id) or master_id, master_id
    key = _extract_voucher_key(root)
    return key, (key if key and key.isdigit() else None)


def is_confirmed_key(tally_guid: str | None) -> bool:
    """False for the `pushed:<number>` placeholder written when Tally gave no key back."""
    return bool(tally_guid) and not str(tally_guid).startswith("pushed:")


def process_push_result(
    session: Session,
    job: TallySyncJob,
    *,
    ok: bool,
    tally_response: str | None,
    agent_error: str | None,
    error_code: str | None = None,
) -> None:
    """Advance `job` to `ok` or `error`. Idempotent: a second call on a
    terminal job is a no-op.
    """
    if job.status in ("ok", "error"):
        return

    if job.kind == "push_items":  # supplier-catalog stock items (S5): own response rules
        from app.services.catalog.tally_items import process_items_result

        process_items_result(
            session, job, ok=ok, tally_response=tally_response, agent_error=agent_error
        )
        return

    if not ok:
        # The agent failed without a word from Tally (a timeout after sending, say): the voucher may
        # be there. A Tally error (below) is definite; this is not (S0.6).
        # An agent stop for the company happened before anything was sent (S0.3): nothing to check.
        if job.kind in ("push_sales", "push_purchase") and error_code not in HARD_STOPS:
            job.outcome_unknown = True
        complete_job_error(session, job, error=agent_error or "agent reported a failure")
        return

    if not tally_response:
        if job.kind in ("push_sales", "push_purchase"):
            job.outcome_unknown = True
        complete_job_error(session, job, error="agent result missing tally_response")
        return

    try:
        root = etree.fromstring(tally_response.encode("utf-8"))
    except etree.XMLSyntaxError as e:
        complete_job_error(session, job, error=f"could not parse Tally's response: {e}")
        return

    line_error = _extract_line_error(root)
    if line_error:
        complete_job_error(session, job, error=line_error)
        return

    created = _extract_created_count(root)
    if created < 1:
        complete_job_error(
            session, job, error="Tally reported no voucher created (no LINEERROR either)"
        )
        return

    if job.kind == "push_receipt":
        company = session.get(TallyCompany, job.company_id)
        key, master_id = voucher_identity(root, company.company_guid if company else None)
        complete_push_ok(
            session,
            job,
            tally_guid=key or f"pushed:{job.entity_id}",
            checksum=str((job.counts or {}).get("checksum") or ""),
            tally_masterid=master_id,
        )
        log.info("tally push %s: receipt created for payment %s", job.id, job.entity_id)
        return

    if job.kind == "push_purchase":
        bill = session.get(InwardBill, job.entity_id) if job.entity_id else None
        if bill is None:
            complete_job_error(session, job, error="the pushed bill no longer exists")
            return
        company = session.get(TallyCompany, job.company_id)
        key, master_id = voucher_identity(root, company.company_guid if company else None)
        voucher_key = key or f"pushed:{bill.bill_no or bill.id}"
        checksum = bill_checksum(bill)
        complete_push_ok(
            session, job, tally_guid=voucher_key, checksum=checksum, tally_masterid=master_id
        )
        log.info("tally push %s: voucher created for inward bill %s", job.id, bill.id)
        return

    invoice = session.get(Invoice, job.entity_id) if job.entity_id else None
    if invoice is None:
        complete_job_error(session, job, error="the pushed invoice no longer exists")
        return

    company = session.get(TallyCompany, job.company_id)
    key, master_id = voucher_identity(root, company.company_guid if company else None)
    voucher_key = key or f"pushed:{invoice.number}"
    checksum = invoice_checksum(invoice)
    complete_push_ok(
        session, job, tally_guid=voucher_key, checksum=checksum, tally_masterid=master_id
    )
    log.info("tally push %s: voucher created for invoice %s", job.id, invoice.id)

"""Raw capture of Tally voucher exports (plan P0, `docs/PLAN-tally-voucher-import-and-party-prices.md`).

A `pull_vouchers` job asks the companion agent to send ONE read-only Export request to the
Tally gateway and park the unparsed XML in R2. Nothing is parsed or applied here: the point is
to collect real exports (fixtures, size, timing) before the parser and the apply step are
written. The same job kind later carries the real month-by-month pull.

The request XML is built here (or supplied by the operator tool), never by a shop user: no
HTTP route enqueues it, only `tools/tally_capture.py` on the VPS. The agent additionally refuses
anything that is not an Export request.

Both request shapes were verified against a live TallyPrime 7.1 (2026-10-28; sample responses in
tests/fixtures/tally_vouchers_dev_sample.xml and tally_bills_receivable_dev_sample.xml). They are
built server-side, so a corrected shape needs no agent release.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime
from xml.sax.saxutils import escape

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentOutboxItem, TallyCompany, TallySyncJob
from app.services.tally.company_identity import company_payload
from app.services.tally.jobs import _NON_TERMINAL, _expire_stuck_job, complete_job_error

log = logging.getLogger("tally.voucher_capture")

KIND = "pull_vouchers"
ENTITY_TYPE = "raw_capture"
_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")

# Every voucher in the period with its ledger lines, item lines and bill allocations. No type
# filter: Sales, Receipt, Credit Note, Journal and the purchase side all come in one pass and
# the backend decides what to use.
_VOUCHERS_TEMPLATE = (
    "<ENVELOPE>"
    "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
    "<TYPE>Collection</TYPE><ID>MEVoucherCapture</ID></HEADER>"
    "<BODY><DESC><STATICVARIABLES>"
    "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    "{company}"
    "<SVFROMDATE>{start}</SVFROMDATE><SVTODATE>{end}</SVTODATE>"
    "</STATICVARIABLES>"
    "<TDL><TDLMESSAGE>"
    '<COLLECTION NAME="MEVoucherCapture" ISMODIFY="No">'
    "<TYPE>Voucher</TYPE>"
    # Named fields only: "*.*" returns the voucher header but NO nested lines, and fetching
    # every nested method is ~6x bigger (65 KB a voucher vs ~10 KB). Verified on TallyPrime 7.1.
    "<FETCH>Date, VoucherTypeName, VoucherNumber, PartyLedgerName, GUID, MasterID, AlterID,"
    " IsCancelled, IsOptional, Narration, Reference,"
    " AllLedgerEntries.LedgerName, AllLedgerEntries.Amount, AllLedgerEntries.IsDeemedPositive,"
    " AllLedgerEntries.IsPartyLedger, AllLedgerEntries.BillAllocations.Name,"
    " AllLedgerEntries.BillAllocations.BillType, AllLedgerEntries.BillAllocations.Amount,"
    " AllLedgerEntries.BillAllocations.BillCreditPeriod,"
    " AllInventoryEntries.StockItemName, AllInventoryEntries.BilledQty,"
    " AllInventoryEntries.ActualQty, AllInventoryEntries.Rate, AllInventoryEntries.Amount,"
    " AllInventoryEntries.Discount, AllInventoryEntries.RateUnit</FETCH>"
    "</COLLECTION>"
    "</TDLMESSAGE></TDL>"
    "</DESC></BODY></ENVELOPE>"
)

# Open (pending) bills of any age, with their due dates.
_BILLS_RECEIVABLE_TEMPLATE = (
    "<ENVELOPE>"
    "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
    "<TYPE>Data</TYPE><ID>Bills Receivable</ID></HEADER>"
    "<BODY><DESC><STATICVARIABLES>"
    "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    "{company}"
    "<SVTODATE>{end}</SVTODATE>"
    "</STATICVARIABLES></DESC></BODY></ENVELOPE>"
)


def _company_var(company_name: str) -> str:
    return f"<SVCURRENTCOMPANY>{escape(company_name)}</SVCURRENTCOMPANY>" if company_name else ""


def _tally_date(d: date) -> str:
    return d.strftime("%Y%m%d")


# The two light queries behind the refill (`light_masters.py`): every ledger and every stock item with its Tally
# id, group or unit and closing figures. Verified on a live TallyPrime 7.1 (2026-10-31). A Tally "Stock Summary"
# report request was tried first and is NOT used: asked this way it answers with a single rolled-up row, not
# one row per item.
_COLLECTION_TEMPLATE = (
    "<ENVELOPE>"
    "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
    "<TYPE>Collection</TYPE><ID>{name}</ID></HEADER>"
    "<BODY><DESC><STATICVARIABLES>"
    "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    "{company}"
    "{as_on}"
    "</STATICVARIABLES>"
    "<TDL><TDLMESSAGE>"
    '<COLLECTION NAME="{name}" ISMODIFY="No"><TYPE>{kind}</TYPE><FETCH>{fetch}</FETCH></COLLECTION>'
    "</TDLMESSAGE></TDL>"
    "</DESC></BODY></ENVELOPE>"
)


def _collection(
    name: str,
    kind: str,
    fetch: str,
    company_name: str,
    as_on: date | None,
    alter_after: int | None = None,
) -> str:
    """`alter_after` asks only for records whose AlterID is above it (verified live: a ledger query above
    217 returned the 5 changed ledgers of 12, a stock-item query above 1540 returned 1,169 of 2,339)."""
    xml = _COLLECTION_TEMPLATE.format(
        name=name,
        kind=kind,
        fetch=fetch,
        company=_company_var(company_name),
        as_on=f"<SVTODATE>{_tally_date(as_on)}</SVTODATE>" if as_on else "",
    )
    if alter_after is not None:
        xml = xml.replace(
            "</COLLECTION></TDLMESSAGE>",
            f"<FILTER>MEAlter</FILTER></COLLECTION>"
            f'<SYSTEM TYPE="Formulae" NAME="MEAlter">$AlterID &gt; {int(alter_after)}</SYSTEM></TDLMESSAGE>',
            1,
        )
    return xml


def build_groups_request(company_name: str, as_on: date | None = None) -> str:
    return _collection("MEGroups", "Group", "Name, Parent", company_name, as_on)


def build_ledgers_request(
    company_name: str, as_on: date | None = None, alter_after: int | None = None
) -> str:
    return _collection(
        "MELedgers",
        "Ledger",
        "Name, Guid, Parent, ClosingBalance, OpeningBalance, AlterID",
        company_name,
        as_on,
        alter_after,
    )


def build_stock_items_request(
    company_name: str, as_on: date | None = None, alter_after: int | None = None
) -> str:
    return _collection(
        "MEStockItems",
        "StockItem",
        "Name, Guid, Parent, BaseUnits, ClosingBalance, ClosingValue, ClosingRate, AlterID",
        company_name,
        as_on,
        alter_after,
    )


def build_vouchers_request(company_name: str, start: date, end: date) -> str:
    if end < start:
        raise ValueError("end date is before start date")
    return _VOUCHERS_TEMPLATE.format(
        company=_company_var(company_name), start=_tally_date(start), end=_tally_date(end)
    )


def build_bills_receivable_request(company_name: str, as_on: date) -> str:
    return _BILLS_RECEIVABLE_TEMPLATE.format(
        company=_company_var(company_name), end=_tally_date(as_on)
    )


def is_export_request(xml: str) -> bool:
    """True only for a read-only Export envelope. The agent applies the same test."""
    low = xml.lower()
    return (
        "<tallyrequest>export</tallyrequest>" in low
        and "<tallyrequest>import" not in low
        and "<importdata" not in low
    )


def assert_no_capture_in_flight(session: Session, tenant_id: str) -> None:
    running = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.tenant_id == tenant_id,
            TallySyncJob.kind == KIND,
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if running is not None and not _expire_stuck_job(session, running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A voucher capture is already in progress for this firm.",
        )


def enqueue_voucher_capture(
    session: Session, company: TallyCompany, *, label: str, request_xml: str
) -> TallySyncJob:
    """Create the job and its agent outbox item. The caller has run the guards."""
    if company.shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Link a companion-agent install to this Tally company first.",
        )
    if not _LABEL_RE.match(label):
        raise ValueError("label must be 1-60 characters of letters, digits, dot, dash, underscore")
    if not is_export_request(request_xml):
        raise ValueError("only a read-only Export request can be captured")

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="in",
        kind=KIND,
        entity_type=ENTITY_TYPE,
        status="queued",
        counts={"label": label},
    )
    session.add(job)
    session.flush()

    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": KIND,
            **company_payload(company),
            "label": label,
            "export_xml": request_xml,
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()
    job.outbox_item_id = outbox.id
    session.flush()
    return job


def process_capture_result(
    session: Session,
    job: TallySyncJob,
    *,
    ok: bool,
    r2_key: str | None,
    meta_json: str | None,
    agent_error: str | None,
) -> None:
    """Advance a capture job. The agent reports `{"label", "bytes", "ms"}` as JSON in the
    small inline field; the XML itself is already in R2 under `r2_key`. Idempotent, and never
    raises back at the agent."""
    if job.status in ("ok", "error"):
        return
    if not ok:
        complete_job_error(session, job, error=agent_error or "agent reported a failure")
        return
    if not r2_key:
        complete_job_error(session, job, error="agent result missing r2_key")
        return

    counts = dict(job.counts or {})
    if meta_json:
        try:
            meta = json.loads(meta_json)
            if isinstance(meta, dict):
                for k in ("bytes", "ms"):
                    if isinstance(meta.get(k), int):
                        counts[k] = meta[k]
        except ValueError:
            log.warning("capture %s: unreadable agent meta", job.id)

    job.r2_key = r2_key
    job.counts = counts
    job.status = "ok"
    job.completed_at = datetime.now(UTC)
    session.flush()

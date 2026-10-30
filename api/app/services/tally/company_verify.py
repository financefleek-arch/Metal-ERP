"""Which company is this, really? (S0.4, gap A2)

`tally_company.company_name` is text an operator typed. If someone renames the company in Tally, or
opens a different company that happens to share the name, our links and pushes would quietly point at
the wrong books. Tally's own GUID for the company survives a rename, so we ask Tally which companies
the agent's gateway has open (name, GUID, books-from date), and compare with what we stored.

It is a read-only Export of the Company collection, which needs no company selected, so it rides the
agent's existing read-only export action (the same one voucher capture and the S0.6 check use): no
new agent release.

Outcomes, kept on the company so the Tally page can show them:
  verified   the open company matches (and, first time, its GUID is stored)
  renamed    the stored GUID is open under a different name; we keep our name and say so
  different  a company with our name is open, but its GUID is not the one we stored
  not_open   nothing we can match is open in the Tally this agent reaches
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from fastapi import HTTPException, status
from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backup_storage import R2NotConfigured, get_object
from app.models import AgentOutboxItem, BackupShop, TallyCompany, TallySyncJob
from app.services.tally.jobs import _NON_TERMINAL, _expire_stuck_job, complete_job_error
from app.services.tally.voucher_capture import is_export_request

log = logging.getLogger("tally.company_verify")

KIND = "verify_company"

_REQUEST = (
    "<ENVELOPE>"
    "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
    "<TYPE>Collection</TYPE><ID>MECompanies</ID></HEADER>"
    "<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    "</STATICVARIABLES>"
    "<TDL><TDLMESSAGE>"
    '<COLLECTION NAME="MECompanies" ISMODIFY="No"><TYPE>Company</TYPE>'
    "<FETCH>Name, GUID, StartingFrom, BooksFrom, MasterID</FETCH></COLLECTION>"
    "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
)


class VerifyFailed(ValueError):
    """Tally's answer could not be read as a list of companies."""


@dataclass(frozen=True)
class OpenCompany:
    name: str
    guid: str
    books_from: date | None


def build_request() -> str:
    return _REQUEST


def _text(el: etree._Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _tally_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def parse_companies(raw: bytes) -> list[OpenCompany]:
    """Companies in Tally's answer. Raises `VerifyFailed` if it is an error or not an answer."""
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError as e:
        raise VerifyFailed(f"could not read Tally's answer: {e}") from e
    err = root.find(".//LINEERROR")
    if err is not None and (err.text or "").strip():
        raise VerifyFailed(err.text.strip())
    if (root.findtext(".//HEADER/STATUS") or "").strip() != "1":
        raise VerifyFailed("Tally did not answer")
    out: list[OpenCompany] = []
    for c in root.iter("COMPANY"):
        guid = _text(c, "GUID")
        name = _text(c, "NAME") or (c.get("NAME") or "").strip()
        if not guid or not name:
            continue  # the <CMPINFO><COMPANY>0</COMPANY> counter is not a company
        out.append(
            OpenCompany(
                name=name,
                guid=guid,
                books_from=_tally_date(_text(c, "BOOKSFROM") or _text(c, "STARTINGFROM")),
            )
        )
    return out


def judge(company: TallyCompany, open_companies: list[OpenCompany]) -> tuple[str, str, OpenCompany | None]:
    """(state, plain-text note, the open company we matched or None). Pure: changes nothing."""
    ours = company.company_name.strip()
    names = ", ".join(f'"{c.name}"' for c in open_companies) or "none"
    by_name = next((c for c in open_companies if c.name.casefold() == ours.casefold()), None)
    stored = (company.company_guid or "").strip()

    if stored:
        by_guid = next((c for c in open_companies if c.guid == stored), None)
        if by_guid is not None and by_guid.name.casefold() == ours.casefold():
            return "verified", f'"{by_guid.name}" is open and is the company we linked.', by_guid
        if by_guid is not None:
            return (
                "renamed",
                f'The company we linked is now called "{by_guid.name}" in Tally, '
                f'but this firm still says "{ours}". Pushes use "{ours}" and Tally will refuse '
                "them until the name here is changed to match.",
                by_guid,
            )
        if by_name is not None:
            return (
                "different",
                f'A company called "{ours}" is open, but it is not the one we linked '
                "earlier (its Tally id is different). Check that this is the right books "
                "before pushing anything.",
                by_name,
            )
        return (
            "not_open",
            f'The company we linked ("{ours}") is not open in the Tally this agent reaches. '
            f"Open now: {names}.",
            None,
        )

    if by_name is not None:
        return "verified", f'"{by_name.name}" is open and is the company we linked.', by_name
    return (
        "not_open",
        f'"{ours}" is not open in the Tally this agent reaches. Open now: {names}.',
        None,
    )


def _apply_verdict(
    company: TallyCompany,
    state: str,
    note: str,
    matched: OpenCompany | None,
    now: datetime,
) -> bool:
    """Write a verdict onto the company. Returns whether anything changed (so a heartbeat that sees
    the same thing every minute does not rewrite the row)."""
    changed = company.verify_state != state or company.verify_note != note[:400]
    company.verify_state = state
    company.verify_note = note[:400]
    if matched is not None and state in ("verified", "renamed"):
        if company.company_guid != matched.guid or company.books_from != matched.books_from:
            changed = True
        company.company_guid = matched.guid
        company.books_from = matched.books_from
    if changed:
        company.verified_at = now
    return changed


def apply_open_companies(session: Session, shop: BackupShop, open_companies: list[OpenCompany]) -> None:
    """The agent's checkin listed what is open. Remember it, and bring every company linked to this
    agent up to date: the GUID is learned by itself the first time exactly one open company has our
    name, and a rename or a same-named other company is noticed without anyone pressing Verify.

    An empty list (nothing open) says nothing about which company is linked, so it only refreshes the
    stored list.
    """
    now = datetime.now(UTC)
    shop.tally_companies = [
        {"name": c.name, "guid": c.guid, "books_from": c.books_from.isoformat() if c.books_from else None}
        for c in open_companies
    ]
    shop.tally_companies_at = now
    if not open_companies:
        return
    linked = session.scalars(select(TallyCompany).where(TallyCompany.shop_id == shop.id)).all()
    for company in linked:
        state, note, matched = judge(company, open_companies)
        _apply_verdict(company, state, note, matched, now)
    session.flush()


def relink_company(
    session: Session, company: TallyCompany, shop: BackupShop | None, guid: str
) -> TallyCompany:
    """Point the link at one of the companies the agent last saw open: take its name and GUID. Covers
    both "Tally renamed it, use the new name" and "this is the right company, not the one we had"."""
    open_now = {c.get("guid"): c for c in (shop.tally_companies or [])} if shop else {}
    picked = open_now.get(guid)
    if picked is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That company is not open in Tally right now. Open it, wait a minute, then pick it.",
        )
    books = picked.get("books_from")
    company.company_name = picked["name"]
    company.company_guid = guid
    company.books_from = date.fromisoformat(books) if books else None
    company.verify_state = "verified"
    company.verify_note = f'"{picked["name"]}" is open and is the company we linked.'
    company.verified_at = datetime.now(UTC)
    session.flush()
    return company


def enqueue_verify_company(session: Session, company: TallyCompany) -> TallySyncJob:
    if company.shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Link a companion-agent install to this Tally company first.",
        )
    running = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.tenant_id == company.tenant_id,
            TallySyncJob.kind == KIND,
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if running is not None and not _expire_stuck_job(session, running):
        return running  # a double click gets the same check back

    request_xml = build_request()
    if not is_export_request(request_xml):  # the agent refuses anything else
        raise ValueError("a company check must be a read-only Export request")

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="in",
        kind=KIND,
        status="queued",
    )
    session.add(job)
    session.flush()
    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": "pull_vouchers",  # the agent's existing read-only export action
            "label": "company",
            "export_xml": request_xml,
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()
    job.outbox_item_id = outbox.id
    session.flush()
    return job


def process_verify_result(
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
        log.exception("company check %s: R2 download failed", job.id)
        complete_job_error(session, job, error=f"could not download Tally's answer: {e}")
        return

    company = session.get(TallyCompany, job.company_id)
    if company is None:
        complete_job_error(session, job, error="the Tally company no longer exists")
        return
    try:
        open_companies = parse_companies(raw)
    except VerifyFailed as e:
        complete_job_error(session, job, error=f"Could not read which companies Tally has open ({e}).")
        return

    state, note, matched = judge(company, open_companies)
    now = datetime.now(UTC)
    _apply_verdict(company, state, note, matched, now)
    company.verified_at = now  # an explicit Verify always stamps the time, even if nothing changed
    job.counts = {"state": state, "open": [c.name for c in open_companies]}
    job.status = "ok"
    job.completed_at = now
    session.flush()

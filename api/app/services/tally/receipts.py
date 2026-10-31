"""Push a recorded payment to Tally as a Receipt voucher (S4.4, decision D-7).

Shape verified live against TallyPrime on 2026-10-31 (dev company "Fleek"; vouchers 29 and 30):
a Receipt with the party ledger credited and the cash or bank ledger debited posts, and a
`BILLALLOCATIONS.LIST` of type "Agst Ref" whose NAME is one of our invoice numbers reduces that bill in
Tally (bill 9104 went from 100.00 to 60.00 outstanding after a 40.00 receipt). An unapplied amount
goes in as a "New Ref" with our own receipt reference, which Tally keeps as an advance.

A payment can settle invoices in more than one company, and a Receipt posts to ONE company, so a
payment is pushed once per company: the allocations of that company's invoices, plus (in the firm's
default company) whatever was taken on account.

The agent runs it with its existing `push_sales` action (it only posts `voucher_xml` to the gateway),
so no agent release is needed; the backend tells the jobs apart by `job.kind == "push_receipt"`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException, status
from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentOutboxItem,
    Invoice,
    Party,
    Payment,
    TallyCompany,
    TallyLink,
    TallySyncJob,
)
from app.models._mixins import AllocationType, PaymentMode, PaymentStatus
from app.services.tally.companies import get_default_company
from app.services.tally.company_identity import company_payload
from app.services.tally.links import tally_name_for
from app.services.tally.push_readiness import PushBlocker, _wrong_company_blockers

KIND = "push_receipt"
ENTITY = "payment"
_UDF_NS = "TallyUDF"
_BANK_MODES = (PaymentMode.upi, PaymentMode.bank, PaymentMode.cheque)


@dataclass(frozen=True)
class Part:
    """One allocation of a receipt: against an invoice (its number is the Tally bill name), or on
    account (invoice None)."""

    invoice_id: str | None
    bill_name: str | None
    amount: Decimal


def receipt_reference(payment: Payment) -> str:
    """Our receipt number as it sits in the voucher's Reference field; unique per firm."""
    return f"ME/R/{payment.voucher_no if payment.voucher_no is not None else payment.id}"


def _money(d: Decimal) -> str:
    return f"{Decimal(d):.2f}"


def split_by_company(
    session: Session, payment: Payment
) -> dict[str | None, list[Part]]:
    """company id -> that company's parts of the payment. An invoice's part goes to the invoice's own
    company (its default if it has none); the on-account part goes to the firm's default company."""
    default = get_default_company(session, payment.tenant_id)
    default_id = default.id if default is not None else None
    out: dict[str | None, list[Part]] = {}
    for al in payment.allocations:
        if al.type == AllocationType.against_invoice and al.invoice_id:
            inv = session.get(Invoice, al.invoice_id)
            company_id = (inv.tally_company_id if inv is not None else None) or default_id
            name = str(inv.number) if inv is not None and inv.number is not None else None
            out.setdefault(company_id, []).append(Part(al.invoice_id, name, Decimal(al.amount)))
        else:
            out.setdefault(default_id, []).append(Part(None, None, Decimal(al.amount)))
    return out


def receipt_ledger(company: TallyCompany, payment: Payment) -> str | None:
    """The cash or bank ledger of `company` the money went to, from the payment mode (D-7)."""
    ledger_map = company.ledger_map or {}
    key = "bank_ledger" if payment.mode in _BANK_MODES else "cash_ledger"
    return ledger_map.get(key) or None


def payment_checksum(payment: Payment, parts: list[Part]) -> str:
    """A cheap fingerprint of what was pushed, so an unchanged re-push is a no-op."""
    blob = json.dumps(
        [payment.id, str(payment.date), _money(payment.amount), payment.mode]
        + [[p.invoice_id, p.bill_name, _money(p.amount)] for p in parts],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def receipt_blockers(
    session: Session, payment: Payment, company: TallyCompany | None
) -> list[PushBlocker]:
    if payment.status != PaymentStatus.posted:
        return [
            PushBlocker(
                "payment_reversed",
                "This payment was reversed, so it is not sent to Tally. Reverse it in Tally by hand.",
            )
        ]
    if company is None:
        return [PushBlocker("no_tally_company", "This firm has no Tally company linked yet.")]
    out: list[PushBlocker] = []
    if company.status != "active":
        out.append(
            PushBlocker(
                "company_disabled",
                f"The Tally company {company.company_name} is switched off for this firm.",
            )
        )
    if not company.shop_id:
        out.append(
            PushBlocker(
                "no_tally_company",
                "This firm's Tally company has no companion agent linked yet.",
            )
        )
    if not (company.company_name or "").strip():
        out.append(PushBlocker("no_tally_company", "The Tally company has no name set."))
    out.extend(_wrong_company_blockers(company))
    if receipt_ledger(company, payment) is None:
        which = "Bank" if payment.mode in _BANK_MODES else "Cash"
        out.append(
            PushBlocker(
                "ledger_map_incomplete",
                f"The {which} ledger isn't mapped for {company.company_name}, so the receipt "
                "cannot name where the money went.",
            )
        )
    party = session.get(Party, payment.party_id)
    if party is None:
        out.append(PushBlocker("party_not_linked", "This payment has no customer."))
    else:
        from app.services.tally.links import is_linked

        if not is_linked(session, company, "party", party):
            out.append(
                PushBlocker(
                    "party_not_linked",
                    f"Customer '{party.legal_name}' isn't linked to Tally yet — pull masters after "
                    "adding it in Tally.",
                )
            )
    return out


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def build_receipt_envelope(
    payment: Payment,
    parts: list[Part],
    *,
    party_name: str,
    cash_or_bank_ledger: str,
    company_name: str | None = None,
) -> etree._Element:
    """The Import-Data envelope for one Receipt voucher (shape verified live, see module docstring)."""
    if not parts:
        raise ValueError("a receipt needs at least one allocation")
    total = sum((p.amount for p in parts), Decimal("0"))
    day = payment.date.strftime("%Y%m%d")
    reference = receipt_reference(payment)

    env = etree.Element("ENVELOPE", nsmap={"UDF": _UDF_NS})
    header = _sub(env, "HEADER")
    _sub(header, "TALLYREQUEST", "Import Data")
    body = _sub(env, "BODY")
    importdata = _sub(body, "IMPORTDATA")
    reqdesc = _sub(importdata, "REQUESTDESC")
    _sub(reqdesc, "REPORTNAME", "Vouchers")
    if company_name:
        _sub(_sub(reqdesc, "STATICVARIABLES"), "SVCURRENTCOMPANY", company_name)
    reqdata = _sub(importdata, "REQUESTDATA")
    msg = _sub(reqdata, "TALLYMESSAGE")

    vch = etree.SubElement(msg, "VOUCHER", VCHTYPE="Receipt", ACTION="Create")
    _sub(vch, "DATE", day)
    _sub(vch, "EFFECTIVEDATE", day)
    _sub(vch, "VOUCHERTYPENAME", "Receipt")
    _sub(vch, "VOUCHERNUMBER", str(payment.voucher_no) if payment.voucher_no is not None else "")
    _sub(vch, "REFERENCE", reference)
    _sub(vch, "PARTYLEDGERNAME", party_name)
    _sub(vch, "PERSISTEDVIEW", "Accounting Voucher View")
    if payment.ref_no:
        _sub(vch, "NARRATION", f"{payment.mode} ref {payment.ref_no}")
    udf = etree.SubElement(
        vch, f"{{{_UDF_NS}}}METALERP_REF", DESC="METALERP_REF", TYPE="String", ISLIST="No"
    )
    udf.text = f"pay_{payment.id}"

    # the party is credited: ISDEEMEDPOSITIVE No, a positive amount, one bill allocation per part
    party_le = _sub(vch, "ALLLEDGERENTRIES.LIST")
    _sub(party_le, "LEDGERNAME", party_name)
    _sub(party_le, "ISDEEMEDPOSITIVE", "No")
    _sub(party_le, "AMOUNT", _money(total))
    for part in parts:
        bw = _sub(party_le, "BILLALLOCATIONS.LIST")
        if part.bill_name:
            _sub(bw, "NAME", part.bill_name)
            _sub(bw, "BILLTYPE", "Agst Ref")
        else:  # on account: an advance under our own receipt number
            _sub(bw, "NAME", reference)
            _sub(bw, "BILLTYPE", "New Ref")
        _sub(bw, "AMOUNT", _money(part.amount))

    cash_le = _sub(vch, "ALLLEDGERENTRIES.LIST")
    _sub(cash_le, "LEDGERNAME", cash_or_bank_ledger)
    _sub(cash_le, "ISDEEMEDPOSITIVE", "Yes")
    _sub(cash_le, "AMOUNT", f"-{_money(total)}")
    return env


def enqueue_push_receipt(
    session: Session, company: TallyCompany, payment: Payment, parts: list[Part]
) -> TallySyncJob:
    """Queue one Receipt for `company`. The caller has run `receipt_blockers`."""
    checksum = payment_checksum(payment, parts)
    running = session.scalar(
        select(TallySyncJob.id).where(
            TallySyncJob.company_id == company.id,
            TallySyncJob.kind == KIND,
            TallySyncJob.entity_id == payment.id,
            TallySyncJob.status.in_(("queued", "sent", "running")),
        )
    )
    if running is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This payment is already being sent to Tally.",
        )
    link = session.scalar(
        select(TallyLink).where(
            TallyLink.company_id == company.id,
            TallyLink.entity_type == ENTITY,
            TallyLink.entity_id == payment.id,
        )
    )
    if link is not None and link.checksum == checksum:
        prior = session.scalar(
            select(TallySyncJob)
            .where(
                TallySyncJob.company_id == company.id,
                TallySyncJob.kind == KIND,
                TallySyncJob.entity_id == payment.id,
                TallySyncJob.status == "ok",
            )
            .order_by(TallySyncJob.completed_at.desc())
        )
        if prior is not None:
            return prior  # already pushed, unchanged

    party = session.get(Party, payment.party_id)
    ledger = receipt_ledger(company, payment)
    if party is None or ledger is None:  # receipt_blockers() should have stopped this
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This payment cannot be pushed."
        )
    env = build_receipt_envelope(
        payment,
        parts,
        party_name=tally_name_for(session, company, "party", party),
        cash_or_bank_ledger=ledger,
        company_name=company.company_name,
    )
    xml = etree.tostring(env, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="out",
        kind=KIND,
        entity_type=ENTITY,
        entity_id=payment.id,
        status="queued",
        counts={"checksum": checksum, "parts": len(parts)},
    )
    session.add(job)
    session.flush()
    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            # the agent's own transport action: it posts voucher_xml and reports Tally's answer
            "action": "push_sales",
            **company_payload(company),
            "voucher_xml": xml.decode("utf-8"),
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()
    job.outbox_item_id = outbox.id
    session.flush()
    return job

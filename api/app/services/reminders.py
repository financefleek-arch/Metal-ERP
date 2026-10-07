"""Payment reminders: proposed each morning, sent only when the shop says so.

An invoice is due `tenant.default_credit_days` after its date. Once it has been overdue for one of
the firm's `reminder_days` (default 7, 15, 30) it needs a reminder at that stage. Rules:

  - one message per PARTY, not per invoice: one invoice needing a reminder gets the
    `payment_reminder` template (quoting what is still owed); two or more get the
    `account_statement` template with the statement PDF;
  - each (invoice, stage) is proposed once. Sent or skipped, it is not proposed again until the
    invoice reaches its next stage;
  - a party is not proposed again while a proposal is waiting (or failed), nor within
    `MIN_GAP_DAYS` of a reminder that was sent;
  - `propose` only writes proposals; sending is the shop's decision (`send`), unless the firm chose
    `reminder_auto_send`, in which case `auto_send` sends the ones that have JUST come due;
  - `propose` is safe to run any number of times (a unique key per party and day).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Invoice, Party, PaymentReminder, Tenant
from app.models._mixins import InvoiceStatus
from app.services import whatsapp as wa
from app.services.payments import balance_due_for_invoice

log = logging.getLogger("reminders")

DEFAULT_DAYS = (7, 15, 30)
MIN_GAP_DAYS = 3
AUTO_FRESH_DAYS = 3  # auto-send only if the stage was reached within this many days
AUTO_MAX_PER_RUN = 40  # a hard ceiling per firm per run
_ZERO = Decimal("0.00")
_LIVE = ("proposed", "sent", "skipped")


def parse_days(raw: str | None) -> list[int]:
    out = sorted({int(p) for p in (raw or "").split(",") if p.strip().isdigit() and int(p) > 0})
    return out or list(DEFAULT_DAYS)


def _stage(overdue: int, days: list[int]) -> int | None:
    reached = [d for d in days if overdue >= d]
    return reached[-1] if reached else None


@dataclass
class _Due:
    invoice: Invoice
    balance: Decimal
    overdue: int
    stage: int


def propose(session: Session, tenant: Tenant, as_on: date | None = None) -> int:
    """Write today's proposals for one firm. Returns how many new ones were made."""
    as_on = as_on or date.today()
    days = parse_days(tenant.reminder_days)
    credit_days = int(tenant.default_credit_days or 0)

    open_invoices = session.scalars(
        select(Invoice).where(
            Invoice.tenant_id == tenant.id,
            Invoice.status == InvoiceStatus.final,
            Invoice.party_id.is_not(None),
        )
    ).all()

    existing = session.scalars(
        select(PaymentReminder).where(
            PaymentReminder.tenant_id == tenant.id,
            PaymentReminder.status.in_((*_LIVE, "failed")),
        )
    ).all()
    covered: dict[str, int] = {}  # invoice id -> highest stage already proposed or sent
    waiting: set[str] = set()  # parties with a proposal nobody has decided yet
    last_sent: dict[str, date] = {}
    for r in existing:
        if r.status != "failed":
            for iid in r.invoice_ids or []:
                covered[iid] = max(covered.get(iid, 0), r.stage_days)
        if r.status in ("proposed", "failed"):  # a failed one still needs the shop's eye
            waiting.add(r.party_id)
        elif r.status == "sent" and r.decided_at is not None:
            d = r.decided_at.date()
            last_sent[r.party_id] = max(last_sent.get(r.party_id, d), d)

    by_party: dict[str, list[_Due]] = {}
    for inv in open_invoices:
        balance = balance_due_for_invoice(session, inv)
        if balance <= _ZERO:
            continue
        overdue = (as_on - (inv.date + timedelta(days=credit_days))).days
        stage = _stage(overdue, days)
        if stage is None or covered.get(inv.id, 0) >= stage:
            continue
        by_party.setdefault(str(inv.party_id), []).append(_Due(inv, balance, overdue, stage))

    made = 0
    for party_id, dues in by_party.items():
        if party_id in waiting:
            continue
        sent_on = last_sent.get(party_id)
        if sent_on is not None and (as_on - sent_on).days < MIN_GAP_DAYS:
            continue
        dues.sort(key=lambda x: (x.invoice.date, x.invoice.number or 0))
        single = len(dues) == 1
        row = PaymentReminder(
            tenant_id=tenant.id,
            party_id=party_id,
            kind="invoice" if single else "statement",
            invoice_id=dues[0].invoice.id if single else None,
            invoice_ids=[d.invoice.id for d in dues],
            stage_days=max(d.stage for d in dues),
            amount=sum((d.balance for d in dues), _ZERO),
            oldest_overdue_days=max(d.overdue for d in dues),
            proposed_on=as_on,
            status="proposed",
            dedupe_key=f"{party_id}:{as_on.isoformat()}",
        )
        try:
            with session.begin_nested():  # a second worker or run that got there first is fine
                session.add(row)
                session.flush()
            made += 1
        except IntegrityError:
            continue
    return made


def send(session: Session, reminder: PaymentReminder, *, user_id: str | None) -> PaymentReminder:
    """Send one proposal over WhatsApp and record what happened. A send failure does not raise:
    the proposal is left `failed` with the reason, so the shop can see it and retry."""
    reminder.decided_by_user_id = user_id
    reminder.decided_at = datetime.now(UTC)
    party = session.get(Party, reminder.party_id)

    live: list[Invoice] = []
    for iid in reminder.invoice_ids or []:
        inv = session.get(Invoice, iid)
        is_open = inv is not None and inv.status == InvoiceStatus.final
        if inv is not None and is_open and balance_due_for_invoice(session, inv) > _ZERO:
            live.append(inv)
    if party is None or not live:
        reminder.status = "skipped"
        reminder.error = "Already paid, so no reminder was sent."
        session.flush()
        return reminder

    try:
        if len(live) == 1:
            msg = wa.send_invoice(
                session,
                live[0],
                template_name="payment_reminder",
                amount_override=balance_due_for_invoice(session, live[0]),
            )
        else:
            msg = wa.send_party_statement(session, party.id, period="this_fy")
    except wa.WhatsappError as exc:
        reminder.status = "failed"
        reminder.error = str(exc)[:1000]
        session.flush()
        return reminder
    reminder.status = "sent"
    reminder.error = None
    reminder.whatsapp_message_id = msg.id
    session.flush()
    return reminder


def skip(session: Session, reminder: PaymentReminder, *, user_id: str | None) -> PaymentReminder:
    reminder.status = "skipped"
    reminder.decided_by_user_id = user_id
    reminder.decided_at = datetime.now(UTC)
    reminder.error = None
    session.flush()
    return reminder


def auto_send(session: Session, tenant: Tenant) -> int:
    """For a firm that allows it: send today's proposals that have just come due.

    Older backlog is left for the shop to review. Without that, switching automatic reminders on
    would message every customer who had been overdue for months in one go. A failed send is
    recorded and left for the shop; it is not retried here. Returns how many were sent."""
    waiting = session.scalars(
        select(PaymentReminder)
        .where(
            PaymentReminder.tenant_id == tenant.id,
            PaymentReminder.status == "proposed",
            PaymentReminder.oldest_overdue_days - PaymentReminder.stage_days <= AUTO_FRESH_DAYS,
        )
        .order_by(PaymentReminder.oldest_overdue_days.desc())
        .limit(AUTO_MAX_PER_RUN)
    ).all()
    sent = 0
    for r in waiting:
        send(session, r, user_id=None)
        session.commit()  # what went out stays recorded even if a later one trips
        sent += r.status == "sent"
    return sent


def run_daily(*, auto_send_allowed: bool = True) -> int:
    """The scheduler's job: propose for every firm that switched reminders on, and send for the
    ones that also allow automatic sending (only when `auto_send_allowed`, i.e. working hours).
    Own session. Returns how many proposals were written."""
    from app.db import SessionLocal

    total = 0
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant).where(Tenant.reminder_enabled.is_(True))).all()
        for tenant in tenants:
            try:
                total += propose(session, tenant)
                session.commit()
                if tenant.reminder_auto_send and tenant.reminder_auto_allowed and auto_send_allowed:
                    auto_send(session, tenant)
            except Exception:  # noqa: BLE001 - one firm's trouble must not stop the others
                session.rollback()
                log.exception("reminder proposals failed for tenant %s", tenant.id)
    return total

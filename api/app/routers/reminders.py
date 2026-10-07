"""Payment reminders: see what is due, send or skip it. Nothing here sends on its own; the daily
run (see `services/reminders.py`) only proposes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Invoice, Party, PaymentReminder, Tenant
from app.services import reminders as svc

router = APIRouter(prefix="/api/reminders", tags=["reminders"])

MAX_PER_CALL = 25  # each send is a Meta round trip (plus a PDF upload), so keep a request short


class ReminderOut(BaseModel):
    id: str
    party_id: str
    party_name: str
    phone: str | None
    kind: str  # invoice | statement
    invoice_numbers: list[int]
    amount: Decimal
    oldest_overdue_days: int
    stage_days: int
    proposed_on: date
    status: str  # proposed | failed (| sent | skipped when asked for)
    error: str | None


class IdsBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=MAX_PER_CALL)


class SendResult(BaseModel):
    sent: int
    failed: int
    skipped: int
    reminders: list[ReminderOut]


def _out(session: SessionDep, r: PaymentReminder) -> ReminderOut:
    party = session.get(Party, r.party_id)
    numbers: list[int] = []
    for iid in r.invoice_ids or []:
        inv = session.get(Invoice, iid)
        if inv is not None and inv.number is not None:
            numbers.append(inv.number)
    return ReminderOut(
        id=r.id,
        party_id=r.party_id,
        party_name=party.legal_name if party else "",
        phone=party.phone if party else None,
        kind=r.kind,
        invoice_numbers=sorted(numbers),
        amount=r.amount,
        oldest_overdue_days=r.oldest_overdue_days,
        stage_days=r.stage_days,
        proposed_on=r.proposed_on,
        status=r.status,
        error=r.error,
    )


def _owned(session: SessionDep, tenant_id: str, ids: list[str]) -> list[PaymentReminder]:
    rows = list(
        session.scalars(
            select(PaymentReminder).where(
                PaymentReminder.tenant_id == tenant_id, PaymentReminder.id.in_(ids)
            )
        )
    )
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found")
    return rows


@router.get("", response_model=list[ReminderOut])
def list_reminders(
    session: SessionDep,
    user: CurrentUser,
    state: Annotated[str, Query(pattern="^(open|sent|skipped)$")] = "open",
) -> list[ReminderOut]:
    """`open` = waiting for the shop's decision, plus failed sends to retry."""
    wanted = ("proposed", "failed") if state == "open" else (state,)
    rows = session.scalars(
        select(PaymentReminder)
        .where(PaymentReminder.tenant_id == user.tenant_id, PaymentReminder.status.in_(wanted))
        .order_by(PaymentReminder.oldest_overdue_days.desc(), PaymentReminder.created_at)
        .limit(200)
    ).all()
    return [_out(session, r) for r in rows]


@router.post("/run", response_model=dict[str, int])
def run_now(session: SessionDep, user: WriteUser) -> dict[str, int]:
    """Look for reminders due right now (the daily run does this by itself when switched on)."""
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    return {"proposed": svc.propose(session, tenant)}


@router.post("/send", response_model=SendResult)
def send_reminders(body: IdsBody, session: SessionDep, user: WriteUser) -> SendResult:
    rows = [
        r for r in _owned(session, user.tenant_id, body.ids) if r.status in ("proposed", "failed")
    ]
    for r in rows:
        svc.send(session, r, user_id=user.id)
        session.commit()  # keep what already went out even if a later one trips
    count = {k: sum(1 for r in rows if r.status == k) for k in ("sent", "failed", "skipped")}
    return SendResult(**count, reminders=[_out(session, r) for r in rows])


@router.post("/skip", response_model=dict[str, int])
def skip_reminders(body: IdsBody, session: SessionDep, user: WriteUser) -> dict[str, int]:
    rows = [
        r for r in _owned(session, user.tenant_id, body.ids) if r.status in ("proposed", "failed")
    ]
    for r in rows:
        svc.skip(session, r, user_id=user.id)
    return {"skipped": len(rows)}

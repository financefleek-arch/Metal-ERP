"""The firm's own Tally companies (S1.4): the owner lists, adds, edits, makes default and switches off
their companies, and anyone who can write checks them against Tally.

Everything here is a thin route over `services/tally/companies.py`, `company_verify.py` and
`visibility.py`, which the Fleek-admin proxy calls as well, so the two cannot drift.
Decision (2026-10-31): owners manage their own companies; only the agent record (provision, key,
version) stays with Fleek, because it costs us storage. See `docs/PARITY-tally-company-management.md`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import (
    BackupShop,
    ItemTallyState,
    PartyItemPrice,
    PartyTallySnapshot,
    TallyCompany,
    TallySyncJob,
    User,
)
from app.models._mixins import UserRole
from app.schemas_tally import LedgerMapIn, OpenCompanyOut, RelinkIn, TallySyncJobOut
from app.services import features
from app.services.tally.agent_health import assert_tally_reachable
from app.services.tally.companies import (
    KINDS,
    CompanyError,
    add_company,
    check_unique,
    get_company,
    series_map,
    set_active,
    set_default,
    update_company,
)
from app.services.tally.company_verify import enqueue_verify_company, relink_company
from app.services.tally.jobs import assert_no_pull_in_flight, enqueue_pull_masters
from app.services.tally.refill import DifferenceError, differences, settle_difference
from app.services.tally.refill_job import MAX_MONTHS, is_refill_job, start_refill
from app.services.tally.visibility import can_see_company, visible_companies

router = APIRouter(
    prefix="/api/tally/companies",
    tags=["tally-companies"],
    dependencies=[features.require("tally")],
)


def require_owner(user: CurrentUser) -> User:
    if user.role != UserRole.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can do this"
        )
    return user


OwnerUser = Annotated[User, Depends(require_owner)]


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_name: str
    kind: str
    is_default: bool
    status: str
    # the number series its bills use (the prefix, else the one picked for it)
    series: str = "Sales"
    series_prefix: str | None
    document_label: str | None
    include_in_reminders: bool
    # refresh stock, balances and last rates from Tally once a day while the agent is online
    auto_refresh: bool = False
    # who may see it besides the owner; null = everyone. Shown to the owner only.
    visible_roles: list[str] | None
    connected: bool = False
    ledger_map: dict[str, str]
    books_from: date | None = None
    verify_state: str | None = None
    verify_note: str | None = None
    verified_at: datetime | None = None
    last_masters_pull_at: datetime | None = None


class CompanyIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    kind: str | None = None


class CompanyPatch(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = None
    document_label: str | None = Field(default=None, max_length=40)
    series_prefix: str | None = Field(default=None, max_length=20)
    include_in_reminders: bool | None = None
    auto_refresh: bool | None = None
    visible_roles: list[str] | None = None


def company_out(session: SessionDep, company: TallyCompany, *, owner_view: bool) -> CompanyOut:
    """One company as the screens show it. The owner (and Fleek's operator console, which calls this too)
    sees who else may see it; an accountant does not."""
    out = CompanyOut.model_validate(company)
    out.series = series_map(session, company.tenant_id).get(company.id, "Sales")
    out.connected = company.shop_id is not None
    if not owner_view:
        out.visible_roles = None
    return out


def _out(session: SessionDep, company: TallyCompany, user: User) -> CompanyOut:
    return company_out(session, company, owner_view=user.role == UserRole.owner)


def _mine(session: SessionDep, user: User, company_id: str) -> TallyCompany:
    """A company of this firm that the user may see; anything else is 404, so it is not revealed."""
    company = get_company(session, user.tenant_id, company_id)
    if company is None or not can_see_company(user, company):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _bad(exc: CompanyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("", response_model=list[CompanyOut])
def list_my_companies(user: CurrentUser, session: SessionDep) -> list[CompanyOut]:
    return [
        _out(session, c, user) for c in visible_companies(session, user, include_disabled=True)
    ]


@router.post("", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
def add_my_company(body: CompanyIn, user: OwnerUser, session: SessionDep) -> CompanyOut:
    """Link a Tally company. The firm's Tally agent must be installed first: it is what reads and
    writes Tally, and what we match the name against."""
    shop = session.scalar(select(BackupShop).where(BackupShop.tenant_id == user.tenant_id))
    if shop is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_missing",
                "message": "Install the Tally agent on the shop PC first, then add the company.",
            },
        )
    if body.kind is not None and body.kind not in KINDS:
        raise HTTPException(status_code=422, detail="Unknown company kind.")
    name = body.company_name.strip()
    try:
        check_unique(session, user.tenant_id, name=name)
    except CompanyError as exc:
        raise _bad(exc) from exc
    company = add_company(session, user.tenant_id, name, shop_id=shop.id, kind=body.kind)
    return _out(session, company, user)


@router.patch("/{company_id}", response_model=CompanyOut)
def edit_my_company(
    company_id: str, body: CompanyPatch, user: OwnerUser, session: SessionDep
) -> CompanyOut:
    company = _mine(session, user, company_id)
    try:
        update_company(session, company, body.model_dump(exclude_unset=True))
    except CompanyError as exc:
        raise _bad(exc) from exc
    return _out(session, company, user)


@router.post("/{company_id}/default", response_model=CompanyOut)
def make_default(company_id: str, user: OwnerUser, session: SessionDep) -> CompanyOut:
    company = _mine(session, user, company_id)
    try:
        set_default(session, company)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Switch the company on first.") from exc
    return _out(session, company, user)


class ActiveIn(BaseModel):
    active: bool


@router.post("/{company_id}/active", response_model=CompanyOut)
def switch_company(
    company_id: str, body: ActiveIn, user: OwnerUser, session: SessionDep
) -> CompanyOut:
    company = _mine(session, user, company_id)
    try:
        set_active(session, company, body.active)
    except CompanyError as exc:
        raise _bad(exc) from exc
    return _out(session, company, user)


# ---- what the accountant may also do: keep the link healthy and the ledger names right ----


@router.post("/{company_id}/verify", response_model=TallySyncJobOut, status_code=201)
def verify_my_company(company_id: str, user: WriteUser, session: SessionDep) -> TallySyncJob:
    company = _mine(session, user, company_id)
    assert_tally_reachable(session, company)
    return enqueue_verify_company(session, company)


@router.get("/open/now", response_model=list[OpenCompanyOut])
def open_in_tally(user: WriteUser, session: SessionDep) -> list[dict]:
    """What the agent last saw open in TallyPrime, for the "pick the company" list."""
    shop = session.scalar(select(BackupShop).where(BackupShop.tenant_id == user.tenant_id))
    return list(shop.tally_companies or []) if shop else []


@router.post("/{company_id}/relink", response_model=CompanyOut)
def relink_my_company(
    company_id: str, body: RelinkIn, user: WriteUser, session: SessionDep
) -> CompanyOut:
    company = _mine(session, user, company_id)
    shop = session.get(BackupShop, company.shop_id) if company.shop_id else None
    return _out(session, relink_company(session, company, shop, body.guid), user)


@router.put("/{company_id}/ledger-map", response_model=CompanyOut)
def set_my_ledger_map(
    company_id: str, body: LedgerMapIn, user: WriteUser, session: SessionDep
) -> CompanyOut:
    company = _mine(session, user, company_id)
    merged = dict(company.ledger_map or {})
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None or value.strip() == "":
            merged.pop(key, None)
        else:
            merged[key] = value.strip()
    company.ledger_map = merged
    session.flush()
    return _out(session, company, user)


@router.post("/{company_id}/pull-masters", response_model=TallySyncJobOut, status_code=201)
def pull_my_masters(company_id: str, user: WriteUser, session: SessionDep) -> TallySyncJob:
    company = _mine(session, user, company_id)
    assert_no_pull_in_flight(session, user.tenant_id, company.id)
    assert_tally_reachable(session, company)
    return enqueue_pull_masters(session, company)


# ---- the refill: stock, what parties owe, last sale and last rate, read from Tally ----


class RefreshIn(BaseModel):
    # blank: a full read the first time and once a week, only the changes otherwise
    months: int | None = Field(default=None, ge=1, le=MAX_MONTHS)
    full: bool = False


class RefillOut(BaseModel):
    """What the company holds from Tally, and where the latest refresh stands."""

    items_with_stock: int
    parties_with_balance: int
    outstanding_total: str
    prices: int
    refreshed_at: datetime | None
    running: bool
    step: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    # full | changes: how the latest read went about it
    mode: str | None = None
    full_refill_at: datetime | None = None
    auto_refresh: bool = False
    differences: int = 0  # typed opening balances that differ from Tally's, waiting for a choice
    notes: list[str] = Field(default_factory=list)  # plain sentences about what the latest read had to do
    results: dict = Field(default_factory=dict)


@router.post("/{company_id}/refresh", response_model=TallySyncJobOut, status_code=201)
def refresh_from_tally(
    company_id: str, body: RefreshIn, user: WriteUser, session: SessionDep
) -> TallySyncJob:
    """Read stock, what each party owes and the latest rates from Tally, a step at a time."""
    company = _mine(session, user, company_id)
    assert_tally_reachable(session, company)
    return start_refill(session, company, months=body.months, full=body.full)


@router.get("/{company_id}/refill", response_model=RefillOut)
def refill_status(company_id: str, user: CurrentUser, session: SessionDep) -> RefillOut:
    return refill_summary(session, _mine(session, user, company_id))


def recent_jobs(session: SessionDep, company: TallyCompany, limit: int = 20) -> list[TallySyncJob]:
    """The company's latest Tally jobs, newest first (what a support person reads to see what happened)."""
    return list(
        session.scalars(
            select(TallySyncJob)
            .where(TallySyncJob.company_id == company.id)
            .order_by(TallySyncJob.created_at.desc())
            .limit(max(1, min(limit, 50)))
        )
    )


def refill_summary(session: SessionDep, company: TallyCompany) -> RefillOut:
    cid = company.id
    stock = session.scalar(
        select(func.count()).select_from(ItemTallyState).where(
            ItemTallyState.company_id == cid, ItemTallyState.stock_as_of.is_not(None)
        )
    )
    owed, owed_sum, refreshed = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(PartyTallySnapshot.outstanding), 0),
            func.max(PartyTallySnapshot.refreshed_at),
        ).where(PartyTallySnapshot.company_id == cid, PartyTallySnapshot.outstanding > 0)
    ).one()
    prices = session.scalar(
        select(func.count()).select_from(PartyItemPrice).where(PartyItemPrice.company_id == cid)
    )
    jobs = [
        j
        for j in session.scalars(
            select(TallySyncJob)
            .where(TallySyncJob.company_id == cid, TallySyncJob.kind == "pull_vouchers")
            .order_by(TallySyncJob.created_at.desc())
            .limit(20)
        )
        if is_refill_job(j)
    ]
    latest = jobs[0] if jobs else None
    results: dict = {}
    for j in reversed(jobs):  # oldest first, so the newest chain's figures overwrite older ones
        results.update((j.counts or {}).get("refill") or {})
    return RefillOut(
        items_with_stock=int(stock or 0),
        parties_with_balance=int(owed or 0),
        outstanding_total=str(Decimal(owed_sum or 0).quantize(Decimal("0.01"))),
        prices=int(prices or 0),
        refreshed_at=refreshed,
        running=bool(latest and latest.status in ("queued", "sent", "running")),
        step=(latest.counts or {}).get("step") if latest else None,
        last_status=latest.status if latest else None,
        last_error=latest.error if latest else None,
        mode=(latest.counts or {}).get("mode") if latest else None,
        full_refill_at=company.full_refill_at,
        auto_refresh=company.auto_refresh,
        differences=len(differences(session, company)),
        notes=list((latest.counts or {}).get("notes") or []) if latest else [],
        results=results,
    )


class DifferenceOut(BaseModel):
    party_id: str
    party: str
    ours: str
    tally: str
    as_of: str | None = None


class DifferenceChoice(BaseModel):
    use: str = Field(pattern="^(tally|mine)$")


@router.get("/{company_id}/refill/differences", response_model=list[DifferenceOut])
def refill_differences(company_id: str, user: WriteUser, session: SessionDep) -> list[dict]:
    """Customers whose opening balance was typed here and differs from what Tally says."""
    return differences(session, _mine(session, user, company_id))


@router.post("/{company_id}/refill/differences/{party_id}", status_code=204)
def settle_refill_difference(
    company_id: str, party_id: str, body: DifferenceChoice, user: WriteUser, session: SessionDep
) -> None:
    company = _mine(session, user, company_id)
    try:
        settle_difference(session, company, party_id, body.use)
    except DifferenceError as exc:
        raise _bad(CompanyError(str(exc))) from exc

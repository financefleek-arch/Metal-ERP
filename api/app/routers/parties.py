"""Party (customer / supplier / both) CRUD, scoped to the caller's tenant.

Read side adds derived fields (completeness, document_count, last_txn_at)
and filters (status, completeness=incomplete, dormant). Search `q` fans
out across name (fuzzy on Postgres), address, and phone.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Party, PartyAddress, Tenant
from app.models._mixins import PartyRole, PartyStatus
from app.schemas import (
    PartyAddressIn,
    PartyCreate,
    PartyListItem,
    PartyOut,
    PartyUpdate,
)
from app.schemas_payments import OpenInvoiceForAllocation
from app.services.pagination import finish_page, paginate
from app.services.parties import (
    SEARCH_RESULT_CAP,
    apply_search,
    completeness_for,
    document_count,
    dormant_cutoff,
    dormant_filter,
    is_incomplete,
)
from app.services.payments import balance_due_for_invoice, open_invoices_for_party

router = APIRouter(prefix="/api/parties", tags=["parties"])


def _get_owned(session: SessionDep, tenant_id: str, party_id: str) -> Party:
    party = session.scalar(
        select(Party).where(Party.id == party_id, Party.tenant_id == tenant_id)
    )
    if party is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Party not found")
    return party


def _apply_addresses(party: Party, addresses: list[PartyAddressIn]) -> None:
    party.addresses.clear()
    for a in addresses:
        party.addresses.append(PartyAddress(**a.model_dump()))


def _out(session: SessionDep, party: Party) -> PartyOut:
    return PartyOut(
        id=party.id,
        legal_name=party.legal_name,
        phone=party.phone,
        email=party.email,
        pan=party.pan,
        role=party.role,
        default_state_code=party.default_state_code,
        gstin=party.gstin,
        whatsapp_optin=party.whatsapp_optin,
        status=party.status,
        source=party.source,
        source_ref=party.source_ref,
        last_txn_at=party.last_txn_at,
        addresses=party.addresses,
        completeness=completeness_for(party),
        document_count=document_count(session, party.id),
    )


def _list_item(party: Party) -> PartyListItem:
    return PartyListItem(
        id=party.id,
        legal_name=party.legal_name,
        role=party.role,
        phone=party.phone,
        default_state_code=party.default_state_code,
        gstin=party.gstin,
        status=party.status,
        source=party.source,
        source_ref=party.source_ref,
        last_txn_at=party.last_txn_at,
        completeness=completeness_for(party),
    )


@router.get("", response_model=list[PartyListItem])
def list_parties(
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    q: str | None = Query(default=None, description="fuzzy name / address / phone"),
    role: PartyRole | None = Query(default=None),
    status_: PartyStatus | None = Query(
        default=None, alias="status", description="default: active only"
    ),
    completeness: Literal["incomplete"] | None = Query(default=None),
    dormant: bool = Query(default=False, description="no transaction in the tenant window"),
    limit: int | None = Query(
        default=None, ge=1, description="page size; omit for the whole list"
    ),
    cursor: str | None = Query(default=None, description="opaque next-page token"),
) -> list[PartyListItem]:
    stmt = select(Party).where(Party.tenant_id == user.tenant_id)

    # Status: default hides archived; ?status=archived shows only those.
    if status_ is None:
        stmt = stmt.where(Party.status == PartyStatus.active)
    else:
        stmt = stmt.where(Party.status == status_)

    if role:
        # 'both' parties match either customer or supplier filters.
        stmt = stmt.where(or_(Party.role == role, Party.role == PartyRole.both))

    if dormant:
        tenant = session.get(Tenant, user.tenant_id)
        days = tenant.dormant_party_days if tenant else 180
        stmt = stmt.where(dormant_filter(dormant_cutoff(days)))

    if q:
        # Ranked by fuzzy score — cap, don't page (see items list_items).
        stmt = apply_search(stmt, session, q).limit(SEARCH_RESULT_CAP)
        parties = list(session.scalars(stmt).unique().all())
        if completeness == "incomplete":
            parties = [p for p in parties if is_incomplete(p)]
        return [_list_item(p) for p in parties]

    # `completeness=incomplete` is a post-query Python filter, so keyset
    # paging (page-then-filter) would give short/empty pages — fall back to
    # the full list for that one filter.
    if completeness == "incomplete":
        stmt = stmt.order_by(func.lower(Party.legal_name))
        parties = [p for p in session.scalars(stmt).unique().all() if is_incomplete(p)]
        return [_list_item(p) for p in parties]

    stmt, paginated = paginate(
        stmt,
        order_cols=[func.lower(Party.legal_name), Party.id],
        directions=["asc", "asc"],
        limit=limit,
        cursor=cursor,
    )
    rows = list(session.scalars(stmt).unique().all())
    if paginated:
        rows = finish_page(
            rows,
            limit=limit,
            key_of=lambda p: [p.legal_name.lower(), p.id],
            response=response,
        )
    return [_list_item(p) for p in rows]


@router.post("", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
def create_party(body: PartyCreate, user: WriteUser, session: SessionDep) -> PartyOut:
    dupe = session.scalar(
        select(Party).where(
            Party.tenant_id == user.tenant_id,
            func.lower(Party.legal_name) == body.legal_name.lower().strip(),
        )
    )
    if dupe is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A party named '{body.legal_name}' already exists",
        )

    data = body.model_dump(exclude={"addresses"})
    party = Party(tenant_id=user.tenant_id, **data)
    _apply_addresses(party, body.addresses)
    session.add(party)
    session.flush()
    return _out(session, party)


@router.get("/{party_id}", response_model=PartyOut)
def get_party(party_id: str, user: CurrentUser, session: SessionDep) -> PartyOut:
    return _out(session, _get_owned(session, user.tenant_id, party_id))


@router.patch("/{party_id}", response_model=PartyOut)
def update_party(
    party_id: str, body: PartyUpdate, user: WriteUser, session: SessionDep
) -> PartyOut:
    party = _get_owned(session, user.tenant_id, party_id)
    patch = body.model_dump(exclude_unset=True)
    addresses = patch.pop("addresses", None)

    if "legal_name" in patch:
        new_name = patch["legal_name"].strip()
        clash = session.scalar(
            select(Party).where(
                Party.tenant_id == user.tenant_id,
                Party.id != party.id,
                func.lower(Party.legal_name) == new_name.lower(),
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A party named '{new_name}' already exists",
            )

    for field, value in patch.items():
        setattr(party, field, value)
    if addresses is not None:
        _apply_addresses(party, [PartyAddressIn(**a) for a in addresses])
    session.flush()
    return _out(session, party)


@router.delete("/{party_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_party(party_id: str, user: WriteUser, session: SessionDep) -> None:
    party = _get_owned(session, user.tenant_id, party_id)
    refs = document_count(session, party.id)
    if refs > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{party.legal_name}' is on {refs} document"
                f"{'s' if refs != 1 else ''}. Archive it instead."
            ),
        )
    session.delete(party)


# --------------------------------------------------------------------------
# open invoices — feeds the payment dialog's FIFO-default allocation table
# --------------------------------------------------------------------------


@router.get("/{party_id}/open-invoices", response_model=list[OpenInvoiceForAllocation])
def list_open_invoices(
    party_id: str, user: CurrentUser, session: SessionDep
) -> list[OpenInvoiceForAllocation]:
    _get_owned(session, user.tenant_id, party_id)
    today = date.today()
    out: list[OpenInvoiceForAllocation] = []
    for inv in open_invoices_for_party(session, party_id):
        out.append(
            OpenInvoiceForAllocation(
                invoice_id=inv.id,
                number=inv.number,
                date=inv.date,
                grand_total=inv.grand_total,
                balance_due=balance_due_for_invoice(session, inv),
                days_old=(today - inv.date).days,
            )
        )
    return out

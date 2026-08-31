"""Party (customer / supplier / both) CRUD, scoped to the caller's tenant."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Party, PartyAddress
from app.models._mixins import PartyRole
from app.schemas import (
    PartyAddressIn,
    PartyCreate,
    PartyListItem,
    PartyOut,
    PartyUpdate,
)

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


@router.get("", response_model=list[PartyListItem])
def list_parties(
    user: CurrentUser,
    session: SessionDep,
    q: str | None = Query(default=None, description="name substring"),
    role: PartyRole | None = Query(default=None),
) -> list[Party]:
    stmt = select(Party).where(Party.tenant_id == user.tenant_id)
    if q:
        needle = f"%{q.lower().strip()}%"
        stmt = stmt.where(func.lower(Party.legal_name).like(needle))
    if role:
        # 'both' parties match either customer or supplier filters.
        stmt = stmt.where(or_(Party.role == role, Party.role == PartyRole.both))
    stmt = stmt.order_by(func.lower(Party.legal_name))
    return list(session.scalars(stmt).all())


@router.post("", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
def create_party(body: PartyCreate, user: WriteUser, session: SessionDep) -> Party:
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
    return party


@router.get("/{party_id}", response_model=PartyOut)
def get_party(party_id: str, user: CurrentUser, session: SessionDep) -> Party:
    return _get_owned(session, user.tenant_id, party_id)


@router.patch("/{party_id}", response_model=PartyOut)
def update_party(
    party_id: str, body: PartyUpdate, user: WriteUser, session: SessionDep
) -> Party:
    party = _get_owned(session, user.tenant_id, party_id)
    patch = body.model_dump(exclude_unset=True)
    addresses = patch.pop("addresses", None)
    for field, value in patch.items():
        setattr(party, field, value)
    if addresses is not None:
        _apply_addresses(party, [PartyAddressIn(**a) for a in addresses])
    session.flush()
    return party


@router.delete("/{party_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_party(party_id: str, user: WriteUser, session: SessionDep) -> None:
    party = _get_owned(session, user.tenant_id, party_id)
    session.delete(party)

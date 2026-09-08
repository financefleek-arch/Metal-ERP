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
from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Party, PartyAddress, Tenant
from app.models._mixins import PartyRole, PartyStatus
from app.schemas import (
    PartyAddressIn,
    PartyCreate,
    PartyDuplicate409,
    PartyListItem,
    PartyMatchRef,
    PartyOut,
    PartyResolveResult,
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
from app.services.party_resolution import PartyMatch, _reason, resolve_party
from app.services.payments import (
    balance_due_for_invoice,
    has_ledger_history,
    open_invoices_for_party,
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


def _match_ref_for_party(session: SessionDep, party_id: str, score: float | None) -> PartyMatchRef:
    p = session.get(Party, party_id)
    addr = (
        next((a for a in p.addresses if a.is_default), p.addresses[0])
        if p and p.addresses
        else None
    )
    return PartyMatchRef(
        id=p.id,
        legal_name=p.legal_name,
        gstin=p.gstin,
        phone=p.phone,
        city=addr.city if addr else None,
        last_txn_at=p.last_txn_at,
        status=str(p.status.value if hasattr(p.status, "value") else p.status),
        score=score,
    )


def _dup_409(session: SessionDep, match: PartyMatch, name: str) -> HTTPException:
    """Build the structured 409 for a likely-duplicate create / rename."""
    if match.method in ("gstin", "phone", "exact"):
        body = PartyDuplicate409(
            code="party_exists",
            message=_reason(match, name),
            match=_match_ref_for_party(session, match.party_id, match.confidence),
        )
    else:
        body = PartyDuplicate409(
            code="party_maybe_exists",
            message=_reason(match, name),
            candidates=[
                PartyMatchRef(
                    id=c.party_id,
                    legal_name=c.legal_name,
                    gstin=c.gstin,
                    phone=c.phone,
                    city=c.city,
                    last_txn_at=c.last_txn_at,
                    status=c.status,
                    score=c.score,
                )
                for c in match.candidates
            ],
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=body.model_dump(mode="json")
    )


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
        opening_balance=party.opening_balance,
        opening_balance_as_of=party.opening_balance_as_of,
        opening_balance_locked=has_ledger_history(session, party.id),
        addresses=party.addresses,
        completeness=completeness_for(party),
        document_count=document_count(session, party.id),
    )


def _list_item(session: SessionDep, party: Party) -> PartyListItem:
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
        opening_balance=party.opening_balance,
        opening_balance_locked=has_ledger_history(session, party.id),
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
        return [_list_item(session, p) for p in parties]

    # `completeness=incomplete` is a post-query Python filter, so keyset
    # paging (page-then-filter) would give short/empty pages — fall back to
    # the full list for that one filter.
    if completeness == "incomplete":
        stmt = stmt.order_by(func.lower(Party.legal_name))
        parties = [p for p in session.scalars(stmt).unique().all() if is_incomplete(p)]
        return [_list_item(session, p) for p in parties]

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
    return [_list_item(session, p) for p in rows]


@router.post(
    "",
    response_model=PartyOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": PartyDuplicate409}},
)
def create_party(
    body: PartyCreate,
    user: WriteUser,
    session: SessionDep,
    force: bool = Query(
        default=False,
        description=(
            "proceed past a *fuzzy* 'similar party exists' warning; ignored "
            "for exact-name / GSTIN / phone matches"
        ),
    ),
) -> PartyOut:
    syn = load_synonym_map(session, user.tenant_id)
    key = normalize_name(body.legal_name, syn)
    if not key:
        raise HTTPException(status_code=422, detail="Party name normalises to nothing")

    match = resolve_party(
        session,
        user.tenant_id,
        body.legal_name,
        gstin=body.gstin,
        phone=body.phone,
        synonyms=syn,
    )
    if match.method in ("gstin", "phone", "exact"):
        raise _dup_409(session, match, body.legal_name)
    if match.candidates and not force:
        raise _dup_409(session, match, body.legal_name)

    data = body.model_dump(exclude={"addresses"})
    party = Party(tenant_id=user.tenant_id, legal_name_normalized=key, **data)
    _apply_addresses(party, body.addresses)
    session.add(party)
    session.flush()
    return _out(session, party)


@router.post("/resolve", response_model=PartyResolveResult)
def resolve_party_endpoint(
    user: CurrentUser,
    session: SessionDep,
    name: str = Query(..., min_length=1, description="free-text party name"),
    gstin: str | None = Query(default=None),
    phone: str | None = Query(default=None),
) -> PartyResolveResult:
    """Type-ahead / pre-flight helper: what would this name (+ GSTIN / phone)
    match? Same ladder as the create gate, no side effects. On an exact / hard-
    key hit the one matched party comes back as the sole candidate; on a fuzzy
    pass the ranked candidates come back with their trigram score. SQLite (no
    pg_trgm): a non-exact, non-key query returns `method=None, candidates=[]`.
    """
    match = resolve_party(session, user.tenant_id, name, gstin=gstin, phone=phone)
    if match.party_id is not None and not match.candidates:
        refs = [_match_ref_for_party(session, match.party_id, match.confidence)]
    else:
        refs = [
            PartyMatchRef(
                id=c.party_id,
                legal_name=c.legal_name,
                gstin=c.gstin,
                phone=c.phone,
                city=c.city,
                last_txn_at=c.last_txn_at,
                status=c.status,
                score=c.score,
            )
            for c in match.candidates[:5]
        ]
    return PartyResolveResult(
        method=match.method,
        confidence=match.confidence,
        weak=match.weak,
        candidates=refs,
    )


@router.get("/{party_id}", response_model=PartyOut)
def get_party(party_id: str, user: CurrentUser, session: SessionDep) -> PartyOut:
    return _out(session, _get_owned(session, user.tenant_id, party_id))


@router.patch(
    "/{party_id}",
    response_model=PartyOut,
    responses={409: {"model": PartyDuplicate409}},
)
def update_party(
    party_id: str,
    body: PartyUpdate,
    user: WriteUser,
    session: SessionDep,
    force: bool = Query(
        default=False, description="proceed past a *fuzzy* rename duplicate warning"
    ),
) -> PartyOut:
    party = _get_owned(session, user.tenant_id, party_id)
    patch = body.model_dump(exclude_unset=True)
    addresses = patch.pop("addresses", None)

    if "legal_name" in patch:
        new_name = patch["legal_name"].strip()
        syn = load_synonym_map(session, user.tenant_id)
        key = normalize_name(new_name, syn)
        if not key:
            raise HTTPException(
                status_code=422, detail="Party name normalises to nothing"
            )
        match = resolve_party(
            session,
            user.tenant_id,
            new_name,
            gstin=patch.get("gstin", party.gstin),
            phone=patch.get("phone", party.phone),
            exclude_id=party.id,
            synonyms=syn,
        )
        if match.method in ("gstin", "phone", "exact"):
            raise _dup_409(session, match, new_name)
        if match.candidates and not force:
            raise _dup_409(session, match, new_name)
        patch["legal_name"] = new_name
        party.legal_name_normalized = key

    # Opening balance locks once the party has any finalized invoice or
    # payment — a real change to it after that point is a 409. A no-op patch
    # (same value) is allowed so a generic "save the whole form" still works.
    ob_fields = {"opening_balance", "opening_balance_as_of"} & patch.keys()
    if ob_fields and has_ledger_history(session, party.id):
        changed = any(getattr(party, f) != patch[f] for f in ob_fields)
        if changed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Opening balance is locked once the party has invoices "
                    "or payments."
                ),
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

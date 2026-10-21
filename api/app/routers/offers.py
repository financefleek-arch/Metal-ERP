"""The shop sends its new range to customers on WhatsApp (docs/PLAN-fleek-control-plane.md, composer).

  POST /api/offers/preview            what a send would do: cards, who gets it, cost, what is in the way
  POST /api/offers/send               make the catalogue link, push customers, ask Fleek to start the campaign
  GET  /api/offers/{campaign_id}      how it is going
  POST /api/offers/{campaign_id}/{pause|resume|cancel}

The shop decides and presses Send; Fleek's backend sends from the shop's own number (see services/offers.py).
Same gate as the rest of the catalogue module: a writer on a firm that has it.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import SessionDep
from app.models import Tenant
from app.routers.catalog import CatalogWriteUser, StorageDep
from app.schemas_customer_catalog import CatalogSelection
from app.services import audit, offers

router = APIRouter(prefix="/api/offers", tags=["offers"])


class OfferIn(BaseModel):
    selection: CatalogSelection


class SendIn(OfferIn):
    title: str = Field(default="New range", min_length=1, max_length=120)
    allow_pieces: bool = False


class CardOut(BaseModel):
    item_id: str
    name: str
    price: str
    photo_url: str | None = None


class PreviewOut(BaseModel):
    selected: int
    offered: int
    with_photo: int
    without_photo: int
    template: dict[str, Any] | None
    cards: list[CardOut]
    audience: dict[str, int]
    allowance: dict[str, int] | None
    approx_cost: float
    ready: bool
    blockers: list[str]


def _tenant(session: SessionDep, tenant_id: str) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Firm not found")
    return tenant


def _refuse(exc: offers.OfferRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/preview", response_model=PreviewOut)
def preview(body: OfferIn, session: SessionDep, user: CatalogWriteUser) -> PreviewOut:
    from app.services.media_urls import media_url

    tenant = _tenant(session, user.tenant_id)
    try:
        plan = offers.plan_offer(session, tenant, body.selection)
    except offers.OfferRefused as exc:
        raise _refuse(exc) from exc
    agreed = plan.audience.get("agreed", 0)
    return PreviewOut(
        selected=plan.selected,
        offered=plan.offered,
        with_photo=plan.with_photo,
        without_photo=plan.without_photo,
        template=plan.template,
        cards=[
            CardOut(item_id=c.item_id, name=c.name, price=c.price, photo_url=media_url(c.media_id, "thumb"))
            for c in plan.cards
        ],
        audience=plan.audience,
        allowance=plan.allowance,
        approx_cost=round(agreed * offers.APPROX_RUPEES_PER_MESSAGE, 2),
        ready=plan.ready,
        blockers=plan.blockers,
    )


@router.post("/send", status_code=201)
def send(body: SendIn, session: SessionDep, user: CatalogWriteUser, storage: StorageDep) -> dict[str, Any]:
    tenant = _tenant(session, user.tenant_id)
    try:
        out = offers.send_offer(
            session,
            tenant,
            storage,
            user_id=user.id,
            selection=body.selection,
            title=body.title,
            allow_pieces=body.allow_pieces,
        )
    except offers.OfferRefused as exc:
        raise _refuse(exc) from exc
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="offer",
        entity_id=str(out.get("id") or ""),
        action="send",
        after={"title": body.title, "recipients": out.get("recipients")},
    )
    return out


@router.get("/{campaign_id}")
def status(campaign_id: str, session: SessionDep, user: CatalogWriteUser) -> dict[str, Any]:
    tenant = _tenant(session, user.tenant_id)
    try:
        return offers.campaign_status(tenant, campaign_id)
    except offers.OfferRefused as exc:
        raise _refuse(exc) from exc


@router.post("/{campaign_id}/{action}")
def act(
    campaign_id: str,
    action: Literal["pause", "resume", "cancel"],
    session: SessionDep,
    user: CatalogWriteUser,
) -> dict[str, Any]:
    tenant = _tenant(session, user.tenant_id)
    try:
        out = offers.campaign_action(tenant, campaign_id, action, user.id)
    except offers.OfferRefused as exc:
        raise _refuse(exc) from exc
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="offer",
        entity_id=campaign_id,
        action=action,
    )
    return out

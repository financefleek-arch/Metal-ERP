"""Calls from the Fleek control plane (fleek-backend). See docs/PLAN-fleek-control-plane.md.

Service-to-service only: every route is behind `require_control_plane` (HMAC-signed request, fail closed).
Fleek owns the organisation, the licence and the WhatsApp connection; this app owns the firm's data. The
control plane never reads or writes our tables, it calls these routes.

P1 here: provision a firm for a Fleek organisation, and link a firm that already exists.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import SessionDep
from app.models import Tenant, TenantWhatsappConfig
from app.services import crm_sync, firm_provisioning
from app.services.control_plane_auth import require_control_plane

router = APIRouter(
    prefix="/api/internal/control-plane",
    tags=["control-plane"],
    dependencies=[Depends(require_control_plane)],
)

log = logging.getLogger(__name__)


class ProvisionIn(BaseModel):
    fleek_org_id: str = Field(min_length=1, max_length=64)
    legal_name: str = Field(min_length=2, max_length=200)
    city: str | None = Field(default=None, max_length=100)


class LinkIn(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    fleek_org_id: str = Field(min_length=1, max_length=64)


class FirmLinkOut(BaseModel):
    tenant_id: str
    fleek_org_id: str
    legal_name: str
    created: bool  # True = a new firm was made by this call; False = the firm already existed


class WhatsappConfigIn(BaseModel):
    fleek_org_id: str = Field(min_length=1, max_length=64)
    phone_number_id: str = Field(min_length=1, max_length=40)
    waba_id: str = Field(min_length=1, max_length=40)
    display_phone_number: str | None = Field(default=None, max_length=30)
    is_active: bool = True


class WhatsappConfigOut(BaseModel):
    tenant_id: str
    fleek_org_id: str
    phone_number_id: str
    is_active: bool


def _by_org(session: SessionDep, fleek_org_id: str) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.fleek_org_id == fleek_org_id))


@router.post("/provision", response_model=FirmLinkOut)
def provision(body: ProvisionIn, session: SessionDep) -> FirmLinkOut:
    """Create the firm for a Fleek organisation. Safe to repeat: the same organisation id answers with the
    firm it already made (a retry after a timeout must never create a second firm)."""
    existing = _by_org(session, body.fleek_org_id)
    if existing is not None:
        return FirmLinkOut(
            tenant_id=existing.id,
            fleek_org_id=body.fleek_org_id,
            legal_name=existing.legal_name,
            created=False,
        )
    firm = firm_provisioning.create_firm(
        session, legal_name=body.legal_name, city=body.city, fleek_org_id=body.fleek_org_id
    )
    log.info("control plane provisioned firm %s for org %s", firm.id, body.fleek_org_id)
    return FirmLinkOut(
        tenant_id=firm.id, fleek_org_id=body.fleek_org_id, legal_name=firm.legal_name, created=True
    )


@router.post("/link", response_model=FirmLinkOut)
def link(body: LinkIn, session: SessionDep) -> FirmLinkOut:
    """Attach a Fleek organisation to a firm that already exists (the one-off backfill of today's shops).
    Refuses to move a link: one firm, one organisation, one time."""
    firm = session.get(Tenant, body.tenant_id)
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Firm not found")
    if firm.fleek_org_id == body.fleek_org_id:
        return FirmLinkOut(
            tenant_id=firm.id, fleek_org_id=body.fleek_org_id, legal_name=firm.legal_name, created=False
        )
    if firm.fleek_org_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This firm is already linked to another organisation")
    other = _by_org(session, body.fleek_org_id)
    if other is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This organisation is already linked to another firm")
    firm.fleek_org_id = body.fleek_org_id
    session.flush()
    return FirmLinkOut(
        tenant_id=firm.id, fleek_org_id=body.fleek_org_id, legal_name=firm.legal_name, created=False
    )


@router.put("/whatsapp", response_model=WhatsappConfigOut)
def put_whatsapp(body: WhatsappConfigIn, session: SessionDep) -> WhatsappConfigOut:
    """Keep Metal ERP's copy of the shop's WhatsApp number in step with Fleek, which owns the connection.

    Safe to repeat. A number that another active firm already uses is refused: the webhook finds the
    firm by that number, so it must belong to exactly one."""
    firm = _by_org(session, body.fleek_org_id)
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No firm is linked to this organisation")
    clash = session.scalar(
        select(TenantWhatsappConfig).where(
            TenantWhatsappConfig.phone_number_id == body.phone_number_id,
            TenantWhatsappConfig.tenant_id != firm.id,
            TenantWhatsappConfig.is_active.is_(True),
        )
    )
    if clash is not None and body.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This number is already used by another firm")
    cfg = session.scalar(
        select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == firm.id)
    )
    if cfg is None:
        cfg = TenantWhatsappConfig(
            tenant_id=firm.id,
            phone_number_id=body.phone_number_id,
            waba_id=body.waba_id,
        )
        session.add(cfg)
    cfg.phone_number_id = body.phone_number_id
    cfg.waba_id = body.waba_id
    cfg.display_phone_number = body.display_phone_number
    cfg.is_active = body.is_active
    session.flush()
    return WhatsappConfigOut(
        tenant_id=firm.id,
        fleek_org_id=body.fleek_org_id,
        phone_number_id=cfg.phone_number_id,
        is_active=cfg.is_active,
    )


class ConsentIn(BaseModel):
    fleek_org_id: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=64)
    status: str  # only "opted_out" is accepted: this door can never opt a customer in
    at: datetime | None = None


class ConsentOut(BaseModel):
    changed: bool


@router.put("/consent", response_model=ConsentOut)
def put_consent(body: ConsentIn, session: SessionDep) -> ConsentOut:
    """The CRM reports that a customer opted out (STOP, a stop inside WhatsApp, a complaint). Recorded on the
    party; an opt-out wins."""
    if body.status != "opted_out":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only an opt-out can be reported here")
    firm = _by_org(session, body.fleek_org_id)
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No firm is linked to this organisation")
    return ConsentOut(changed=crm_sync.apply_opt_out(session, firm.id, body.external_id, at=body.at))


class SyncAsk(BaseModel):
    fleek_org_id: str = Field(min_length=1, max_length=64)


class SyncOut(BaseModel):
    sent: int
    status: dict[str, int]
    consent: dict[str, int]
    opted_out_here: int


@router.post("/contacts/sync", response_model=SyncOut)
def sync_contacts(body: SyncAsk, session: SessionDep) -> SyncOut:
    """Fleek asks this firm to push its customers to the CRM now (before a campaign). Safe to repeat."""
    firm = _by_org(session, body.fleek_org_id)
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No firm is linked to this organisation")
    try:
        out = crm_sync.push(session, firm)
    except crm_sync.SyncError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return SyncOut(sent=out.sent, status=out.status, consent=out.consent, opted_out_here=out.opted_out_here)

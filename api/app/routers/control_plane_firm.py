"""Shop-level operator actions for the Fleek control plane (docs/PLAN-fleek-control-plane.md, one console).

Fleek's admin console is the one place an operator manages a Metal ERP shop. It reaches this app's own
operator functions through these signed routes, addressed by the Fleek organisation id (never a Metal ERP
id), so the Metal ERP console can be retired. Same authentication as the rest of
/api/internal/control-plane: HMAC-signed, fail closed.

Each route finds the firm linked to that organisation (404 if none) and calls the operator router's own
function, so a rule (the plan's user limit, the last-owner guard, the pinned-version check) has exactly one
implementation. A user id from another firm is a 404, never reachable from here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select

from app.deps import SessionDep
from app.models import Tenant, User
from app.routers import admin
from app.schemas_admin import (
    AdminUserCreate,
    AdminUserOut,
    AdminUserPatch,
    AgentTargetVersionIn,
    FirmDetail,
    FirmPatch,
    FirmTallyShopOut,
    FirmTallyShopProvisionResult,
    FirmWhatsappTestIn,
    FirmWhatsappTestOut,
    SlipUsageOut,
)
from app.services.control_plane_auth import require_control_plane
from app.services.ocr import slip_usage

router = APIRouter(
    prefix="/api/internal/control-plane/firms/{fleek_org_id}",
    tags=["control-plane"],
    dependencies=[Depends(require_control_plane)],
)


def _firm(session: SessionDep, fleek_org_id: str) -> Tenant:
    firm = session.scalar(select(Tenant).where(Tenant.fleek_org_id == fleek_org_id))
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No firm is linked to this organisation")
    return firm


def _user_of(session: SessionDep, firm: Tenant, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None or user.tenant_id != firm.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


# ---------------------------------------------------------------- the firm and its switches


@router.get("", response_model=FirmDetail)
def get_settings(fleek_org_id: str, session: SessionDep) -> FirmDetail:
    return admin.get_firm(_firm(session, fleek_org_id).id, session)


@router.patch("", response_model=FirmDetail)
def patch_settings(fleek_org_id: str, body: FirmPatch, session: SessionDep) -> FirmDetail:
    return admin.update_firm(_firm(session, fleek_org_id).id, body, session)


@router.post("/whatsapp/test", response_model=FirmWhatsappTestOut)
def whatsapp_test(fleek_org_id: str, body: FirmWhatsappTestIn, session: SessionDep) -> FirmWhatsappTestOut:
    return admin.test_firm_whatsapp(_firm(session, fleek_org_id).id, body, session)


# ---------------------------------------------------------------- users


@router.get("/users", response_model=list[AdminUserOut])
def list_users(fleek_org_id: str, session: SessionDep) -> list[AdminUserOut]:
    return admin.list_firm_users(_firm(session, fleek_org_id).id, session)


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(fleek_org_id: str, body: AdminUserCreate, session: SessionDep) -> AdminUserOut:
    return admin.create_firm_user(_firm(session, fleek_org_id).id, body, session)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def patch_user(fleek_org_id: str, user_id: str, body: AdminUserPatch, session: SessionDep) -> AdminUserOut:
    _user_of(session, _firm(session, fleek_org_id), user_id)
    return admin.update_firm_user(user_id, body, session)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_user(fleek_org_id: str, user_id: str, session: SessionDep) -> None:
    _user_of(session, _firm(session, fleek_org_id), user_id)
    admin.disable_firm_user(user_id, session)


# ---------------------------------------------------------------- slip-reading usage (read only)


@router.get("/slip-usage", response_model=SlipUsageOut)
def get_slip_usage(fleek_org_id: str, session: SessionDep) -> dict:
    return slip_usage.usage_summary(session, _firm(session, fleek_org_id).id)


# ---------------------------------------------------------------- Tally companion agent


@router.get("/tally-shop", response_model=FirmTallyShopOut)
def get_tally(fleek_org_id: str, session: SessionDep) -> FirmTallyShopOut:
    return admin.get_firm_tally_shop(_firm(session, fleek_org_id).id, session)


@router.post("/tally-shop", response_model=FirmTallyShopProvisionResult, status_code=status.HTTP_201_CREATED)
def provision_tally(fleek_org_id: str, session: SessionDep) -> FirmTallyShopProvisionResult:
    return admin.provision_firm_tally_shop(_firm(session, fleek_org_id).id, session)


@router.post("/tally-shop/rotate-key", response_model=FirmTallyShopProvisionResult)
def rotate_tally_key(fleek_org_id: str, session: SessionDep) -> FirmTallyShopProvisionResult:
    return admin.rotate_firm_tally_shop_key(_firm(session, fleek_org_id).id, session)


@router.put("/tally-shop/target-version", response_model=FirmTallyShopOut)
def set_tally_version(fleek_org_id: str, body: AgentTargetVersionIn, session: SessionDep) -> FirmTallyShopOut:
    return admin.set_firm_agent_target_version(_firm(session, fleek_org_id).id, body, session)


@router.get("/tally-shop/installer")
def tally_installer(fleek_org_id: str, session: SessionDep) -> Response:
    return admin.download_firm_installer(_firm(session, fleek_org_id).id, session)

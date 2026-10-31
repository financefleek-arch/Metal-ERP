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
from app.models import TallyCompany, Tenant, User
from app.routers import admin
from app.routers.tally_companies import (
    CompanyOut,
    RefillOut,
    company_out,
    recent_jobs,
    refill_summary,
)
from app.schemas_admin import (
    AdminUserCreate,
    AdminUserOut,
    AdminUserPatch,
    AgentTargetVersionIn,
    FirmDeleteOut,
    FirmDetail,
    FirmPatch,
    FirmTallyShopOut,
    FirmTallyShopProvisionResult,
    FirmWhatsappTestIn,
    FirmWhatsappTestOut,
    SlipUsageOut,
)
from app.schemas_tally import TallySyncJobOut
from app.services import firm_erasure
from app.services.catalog.storage import CatalogStorage, get_storage
from app.services.control_plane_auth import require_control_plane
from app.services.ocr import slip_usage
from app.services.tally.companies import get_company, list_companies

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


# ---------------------------------------------------------------- erase the shop


@router.delete("", response_model=FirmDeleteOut)
def erase_firm(
    fleek_org_id: str, session: SessionDep, storage: CatalogStorage = Depends(get_storage)
) -> FirmDeleteOut:
    """Erase the firm and every trace of it. Fleek has already had the operator confirm the shop's name;
    404 means there is nothing here (already deleted), which Fleek treats as done."""
    firm = _firm(session, fleek_org_id)
    name = firm.legal_name
    try:
        report = firm_erasure.erase_firm(session, firm, storage)
    except firm_erasure.ErasureRefused as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return FirmDeleteOut(
        deleted=name,
        rows_removed=report.total_rows,
        files_removed=report.files_removed,
        files_failed=report.files_failed,
        fleek_org_id=fleek_org_id,
    )


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


# ---------------------------------------------------------------- Tally companies (read only, for support)
#
# The owner manages their own companies in their own app (decision 2026-10-31: avoid unnecessary
# centralization). Fleek's console only looks, so a support person can see what the owner sees. Same
# functions as the owner's routes (`routers/tally_companies.py`), so the two screens cannot drift. Writing
# from here would be a new decision (docs/PARITY-tally-company-management.md).


def _company(session: SessionDep, firm: Tenant, company_id: str) -> TallyCompany:
    company = get_company(session, firm.id, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return company


@router.get("/tally-companies", response_model=list[CompanyOut])
def list_tally_companies(fleek_org_id: str, session: SessionDep) -> list[CompanyOut]:
    firm = _firm(session, fleek_org_id)
    return [
        company_out(session, c, owner_view=True) for c in list_companies(session, firm.id, include_disabled=True)
    ]


@router.get("/tally-companies/{company_id}/refill", response_model=RefillOut)
def get_tally_company_refill(fleek_org_id: str, company_id: str, session: SessionDep) -> RefillOut:
    return refill_summary(session, _company(session, _firm(session, fleek_org_id), company_id))


@router.get("/tally-companies/{company_id}/jobs", response_model=list[TallySyncJobOut])
def get_tally_company_jobs(fleek_org_id: str, company_id: str, session: SessionDep) -> list:
    return recent_jobs(session, _company(session, _firm(session, fleek_org_id), company_id))

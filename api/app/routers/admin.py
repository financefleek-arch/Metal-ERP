"""Platform-admin API — provision client firms and their login accounts.

Operator-only. Every route sits behind `require_platform_admin`, the one
principal permitted to act outside its own tenant. The platform-admin
accounts themselves are created by `tools.make_platform_admin`, never here.

A new firm gets the same catalogue/synonym seed a self-registered firm
gets (`seed_synonyms` + `seed_taxonomy`) so there are no half-set-up
tenants. Users are created with an operator-chosen password, hashed with
Argon2 exactly as `/api/auth/register` does; no endpoint echoes a
plaintext password back.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.deps import SessionDep, require_platform_admin
from app.models import Tenant, TenantWhatsappConfig, User
from app.models._mixins import UserRole
from app.schemas_admin import (
    ASSIGNABLE_ROLES,
    AdminUserCreate,
    AdminUserOut,
    AdminUserPatch,
    FirmCreate,
    FirmDetail,
    FirmListItem,
    FirmPatch,
    FirmWhatsappOut,
    FirmWhatsappUpsert,
)
from app.security import hash_password
from app.seed import seed_synonyms
from app.services.catalogue.seed_taxonomy import seed_taxonomy

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)


def _load_firm(session: SessionDep, firm_id: str) -> Tenant:
    firm = session.get(Tenant, firm_id)
    if firm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firm not found")
    return firm


def _firm_users(firm: Tenant) -> list[User]:
    return sorted(firm.users, key=lambda u: u.created_at)


def _detail(firm: Tenant) -> FirmDetail:
    return FirmDetail(
        id=firm.id,
        legal_name=firm.legal_name,
        city=firm.city,
        gst_enabled=firm.gst_enabled,
        ext_inward_import=firm.ext_inward_import,
        created_at=firm.created_at,
        users=[AdminUserOut.model_validate(u) for u in _firm_users(firm)],
    )


def _assert_not_platform_admin(user: User) -> None:
    """Platform-admin rows are managed only by the CLI, never via this API."""
    if user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform-admin accounts are managed from the CLI",
        )


def _active_owner_ids(firm: Tenant) -> list[str]:
    return [
        u.id for u in firm.users if u.is_active and u.role == UserRole.owner
    ]


# --------------------------------------------------------------------------
# firms
# --------------------------------------------------------------------------


@router.get("/firms", response_model=list[FirmListItem])
def list_firms(session: SessionDep, q: str | None = Query(default=None)) -> list[FirmListItem]:
    stmt = select(Tenant)
    if q and q.strip():
        stmt = stmt.where(Tenant.legal_name.ilike(f"%{q.strip()}%"))
    firms = list(session.scalars(stmt.order_by(func.lower(Tenant.legal_name))).all())

    out: list[FirmListItem] = []
    for f in firms:
        users = list(f.users)
        out.append(
            FirmListItem(
                id=f.id,
                legal_name=f.legal_name,
                city=f.city,
                gst_enabled=f.gst_enabled,
                ext_inward_import=f.ext_inward_import,
                user_count=len(users),
                active_user_count=sum(1 for u in users if u.is_active),
                created_at=f.created_at,
            )
        )
    return out


@router.post("/firms", response_model=FirmDetail, status_code=status.HTTP_201_CREATED)
def create_firm(body: FirmCreate, session: SessionDep) -> FirmDetail:
    firm = Tenant(
        legal_name=body.legal_name.strip(),
        city=(body.city.strip() if body.city else None),
        document_label="Invoice",
    )
    session.add(firm)
    session.flush()

    # Same bootstrap a self-registered firm gets: normalization dictionary
    # first (so seed_taxonomy's name_normalized keys collapse consistently),
    # then the fixed item taxonomy.
    seed_synonyms(session, firm.id)
    session.flush()
    seed_taxonomy(session, firm.id)
    session.flush()

    return _detail(firm)


@router.get("/firms/{firm_id}", response_model=FirmDetail)
def get_firm(firm_id: str, session: SessionDep) -> FirmDetail:
    return _detail(_load_firm(session, firm_id))


@router.patch("/firms/{firm_id}", response_model=FirmDetail)
def update_firm(firm_id: str, body: FirmPatch, session: SessionDep) -> FirmDetail:
    firm = _load_firm(session, firm_id)
    data = body.model_dump(exclude_unset=True)
    if "legal_name" in data and data["legal_name"]:
        firm.legal_name = data["legal_name"].strip()
    if "city" in data:
        firm.city = data["city"].strip() if data.get("city") else None
    if "gst_enabled" in data and data["gst_enabled"] is not None:
        firm.gst_enabled = data["gst_enabled"]
    if "ext_inward_import" in data and data["ext_inward_import"] is not None:
        firm.ext_inward_import = data["ext_inward_import"]
    session.flush()
    return _detail(firm)


# --------------------------------------------------------------------------
# firm WhatsApp config
# --------------------------------------------------------------------------


def _whatsapp_out(cfg: TenantWhatsappConfig | None) -> FirmWhatsappOut:
    if cfg is None:
        return FirmWhatsappOut(configured=False)
    return FirmWhatsappOut(
        configured=True,
        is_active=cfg.is_active,
        phone_number_id=cfg.phone_number_id,
        waba_id=cfg.waba_id,
        display_phone_number=cfg.display_phone_number,
        updated_at=cfg.updated_at,
    )


@router.get("/firms/{firm_id}/whatsapp", response_model=FirmWhatsappOut)
def get_firm_whatsapp(firm_id: str, session: SessionDep) -> FirmWhatsappOut:
    _load_firm(session, firm_id)
    cfg = session.scalar(
        select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == firm_id)
    )
    return _whatsapp_out(cfg)


@router.put("/firms/{firm_id}/whatsapp", response_model=FirmWhatsappOut)
def upsert_firm_whatsapp(
    firm_id: str, body: FirmWhatsappUpsert, session: SessionDep
) -> FirmWhatsappOut:
    _load_firm(session, firm_id)
    cfg = session.scalar(
        select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == firm_id)
    )

    if cfg is None:
        cfg = TenantWhatsappConfig(
            tenant_id=firm_id,
            phone_number_id=body.phone_number_id.strip(),
            waba_id=body.waba_id.strip(),
            display_phone_number=(body.display_phone_number or None),
            is_active=body.is_active,
        )
        session.add(cfg)
    else:
        cfg.phone_number_id = body.phone_number_id.strip()
        cfg.waba_id = body.waba_id.strip()
        cfg.display_phone_number = body.display_phone_number or None
        cfg.is_active = body.is_active

    session.flush()
    return _whatsapp_out(cfg)


@router.delete("/firms/{firm_id}/whatsapp", status_code=status.HTTP_204_NO_CONTENT)
def delete_firm_whatsapp(firm_id: str, session: SessionDep) -> None:
    _load_firm(session, firm_id)
    cfg = session.scalar(
        select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == firm_id)
    )
    if cfg is not None:
        session.delete(cfg)


# --------------------------------------------------------------------------
# firm users
# --------------------------------------------------------------------------


@router.get("/firms/{firm_id}/users", response_model=list[AdminUserOut])
def list_firm_users(firm_id: str, session: SessionDep) -> list[AdminUserOut]:
    firm = _load_firm(session, firm_id)
    return [AdminUserOut.model_validate(u) for u in _firm_users(firm)]


@router.post(
    "/firms/{firm_id}/users",
    response_model=AdminUserOut,
    status_code=status.HTTP_201_CREATED,
)
def create_firm_user(
    firm_id: str, body: AdminUserCreate, session: SessionDep
) -> AdminUserOut:
    firm = _load_firm(session, firm_id)

    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role must be owner, accountant, or viewer",
        )

    email = body.email.lower().strip()
    # Email must be unique across ALL firms so login stays unambiguous —
    # the same rule /api/auth/register enforces.
    clash = session.scalar(select(User).where(func.lower(User.email) == email))
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        tenant_id=firm.id,
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    session.add(user)
    session.flush()
    return AdminUserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_firm_user(
    user_id: str, body: AdminUserPatch, session: SessionDep
) -> AdminUserOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_not_platform_admin(user)

    firm = session.get(Tenant, user.tenant_id)
    assert firm is not None  # FK guarantees it

    data = body.model_dump(exclude_unset=True)

    new_role = data.get("role")
    if new_role is not None and new_role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role must be owner, accountant, or viewer",
        )

    # Last-active-owner guard: block a change that would leave the firm with
    # no active owner (demotion or deactivation of the only one).
    active_owners = set(_active_owner_ids(firm))
    would_demote = (
        new_role is not None and new_role != UserRole.owner and user.role == UserRole.owner
    )
    would_deactivate = data.get("is_active") is False and user.is_active
    if (would_demote or would_deactivate) and active_owners == {user.id}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the firm's only active owner",
        )

    if new_role is not None:
        user.role = new_role
    if "is_active" in data and data["is_active"] is not None:
        user.is_active = data["is_active"]
    if data.get("password"):
        user.password_hash = hash_password(data["password"])

    session.flush()
    return AdminUserOut.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_firm_user(user_id: str, session: SessionDep) -> None:
    """Soft-delete: deactivate the login. Its token stops working at once
    (`get_current_user` checks `is_active`)."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_not_platform_admin(user)

    firm = session.get(Tenant, user.tenant_id)
    assert firm is not None
    if user.is_active and _active_owner_ids(firm) == [user.id]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the firm's only active owner",
        )

    user.is_active = False
    session.flush()

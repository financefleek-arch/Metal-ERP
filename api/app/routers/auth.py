"""Auth routes: register a firm + owner, log in, read the current user."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep
from app.models import Tenant, User
from app.models._mixins import UserRole
from app.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.seed import seed_synonyms
from app.services.catalogue.seed_taxonomy import seed_taxonomy

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, session: SessionDep) -> TokenResponse:
    """Create a new firm (tenant) and its first owner user, return a token.

    Email is unique per tenant in the schema, but at registration there is
    no tenant yet — so we also reject an email already used by ANY user,
    to keep login unambiguous.
    """
    email = body.email.lower().strip()
    exists = session.scalar(select(User).where(func.lower(User.email) == email))
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    tenant = Tenant(legal_name=body.firm_name.strip(), document_label="Invoice")
    session.add(tenant)
    session.flush()

    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(body.password),
        role=UserRole.owner,
    )
    session.add(user)
    session.flush()

    # Seed the name-normalization dictionary FIRST so seed_taxonomy's group
    # name_normalized keys (and every later item) collapse consistently
    # (bartan/Hindi words -> English trade term). Flush so seed_taxonomy's
    # synonym-map query sees them (the session runs autoflush=False).
    seed_synonyms(session, tenant.id)
    session.flush()

    # Seed the fixed item taxonomy: 12 departments + starter brands as
    # item_category rows, ~85 product_group rows. The shop edits from here.
    seed_taxonomy(session, tenant.id)

    token = create_access_token(user_id=user.id, tenant_id=tenant.id, role=str(user.role))
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: SessionDep) -> TokenResponse:
    email = body.email.lower().strip()
    # The schema allows the same email in two tenants; the register / admin
    # provisioning paths enforce global uniqueness, but stay defensive here
    # so a stray duplicate can't turn login into a 500.
    user = session.scalars(
        select(User)
        .where(func.lower(User.email) == email)
        .order_by(User.created_at)
    ).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, role=str(user.role)
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser, session: SessionDep) -> UserOut:
    tenant = session.get(Tenant, user.tenant_id)
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        is_platform_admin=user.is_platform_admin,
        ext_inward_import=bool(tenant and tenant.ext_inward_import),
    )

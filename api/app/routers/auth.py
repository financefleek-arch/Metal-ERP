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

    token = create_access_token(user_id=user.id, tenant_id=tenant.id, role=str(user.role))
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: SessionDep) -> TokenResponse:
    email = body.email.lower().strip()
    user = session.scalar(select(User).where(func.lower(User.email) == email))
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
def me(user: CurrentUser) -> User:
    return user

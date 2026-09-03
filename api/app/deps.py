"""FastAPI dependencies: DB session, current user, role gates.

`get_current_user` decodes the bearer token and loads the User row (so a
deactivated or deleted user is rejected even with a still-valid token).
Every domain query is then scoped to `user.tenant_id`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import BackupShop, User
from app.models._mixins import UserRole
from app.security import JWTError, decode_access_token, hash_shop_key

SessionDep = Annotated[Session, Depends(get_session)]

_bearer = HTTPBearer(auto_error=True)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    session: SessionDep,
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(creds.credentials)
        user_id = payload.get("sub")
    except JWTError:
        raise cred_exc from None
    if not user_id:
        raise cred_exc

    user = session.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise cred_exc
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_platform_admin(user: CurrentUser) -> User:
    """Gate for `/api/admin/*` — firm & user provisioning for the operator.

    A platform admin is the only principal allowed to act outside its own
    `tenant_id`. Everything else keeps the strict per-tenant scoping.
    """
    if not user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin only",
        )
    return user


PlatformAdmin = Annotated[User, Depends(require_platform_admin)]

_WRITE_ROLES = {UserRole.owner, UserRole.accountant}


def require_write(user: CurrentUser) -> User:
    if user.role not in _WRITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires owner or accountant role",
        )
    return user


WriteUser = Annotated[User, Depends(require_write)]


def get_shop(
    session: SessionDep,
    x_shop_key: Annotated[str | None, Header()] = None,
) -> BackupShop:
    """Auth for `/api/tally-agent/*` — machine-to-machine, not a human login.

    A distinct scheme from the JWT bearer used everywhere else: the Windows
    tally-agent tool authenticates with a per-shop API key issued once by
    `tools.make_backup_shop`, sent as `X-Shop-Key`. No JWT/User is involved.
    """
    if not x_shop_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Shop-Key header required"
        )
    shop = session.scalar(
        select(BackupShop).where(BackupShop.api_key_hash == hash_shop_key(x_shop_key))
    )
    if shop is None or not shop.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid shop key")
    return shop


ShopAuth = Annotated[BackupShop, Depends(get_shop)]

"""Request/response models for the platform-admin API (`/api/admin/*`).

Operator-only: create client firms, provision their login accounts, reset
passwords, disable users. No endpoint ever returns a plaintext password —
the operator sets every password explicitly and keeps its own record.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models._mixins import UserRole
from app.schemas import LegalName, OptLegalName

# Roles the operator may assign to a firm user. `owner` and `accountant`
# both carry full read/write today; `viewer` is read-only. The shop-floor
# touchpoint roles are deliberately not offered here.
ASSIGNABLE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.owner, UserRole.accountant, UserRole.viewer}
)


# --------------------------------------------------------------------------
# firms
# --------------------------------------------------------------------------


class FirmListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    city: str | None = None
    gst_enabled: bool
    ext_inward_import: bool
    user_count: int
    active_user_count: int
    created_at: datetime


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    role: UserRole
    is_active: bool
    is_platform_admin: bool
    created_at: datetime


class FirmDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    city: str | None = None
    gst_enabled: bool
    ext_inward_import: bool
    created_at: datetime
    users: list[AdminUserOut]


class FirmCreate(BaseModel):
    legal_name: LegalName
    city: str | None = Field(default=None, max_length=100)


class FirmPatch(BaseModel):
    legal_name: OptLegalName = None
    city: str | None = Field(default=None, max_length=100)
    gst_enabled: bool | None = None
    ext_inward_import: bool | None = None


# --------------------------------------------------------------------------
# firm users
# --------------------------------------------------------------------------


class AdminUserCreate(BaseModel):
    email: EmailStr = Field(max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: UserRole = UserRole.accountant


class AdminUserPatch(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)

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


# --------------------------------------------------------------------------
# firm WhatsApp config
# --------------------------------------------------------------------------


class FirmWhatsappOut(BaseModel):
    """Per-firm number registry. There is no token here — sends use the
    process-wide FleekWA System User token."""

    model_config = ConfigDict(from_attributes=True)

    configured: bool
    is_active: bool = False
    phone_number_id: str | None = None
    waba_id: str | None = None
    display_phone_number: str | None = None
    updated_at: datetime | None = None


class FirmWhatsappUpsert(BaseModel):
    phone_number_id: str = Field(min_length=1, max_length=40)
    waba_id: str = Field(min_length=1, max_length=40)
    display_phone_number: str | None = Field(default=None, max_length=30)
    is_active: bool = True


class FirmWhatsappTestIn(BaseModel):
    """Fire one template send to `to_phone` with dummy data — proves the
    firm's number + token + approved template all line up, no invoice needed.
    Recipient must be reachable (added as a tester on the WABA if the app is
    still in development)."""

    to_phone: str = Field(min_length=8, max_length=20, description="digits, country code ok, no +")
    template_name: str = "invoice_ready"
    with_document: bool = True


class FirmWhatsappTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    template_name: str
    to_phone: str
    wa_message_id: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# firm tally-agent shop (cloud backup sync — a separate product, see
# app/routers/tally_agent.py; a "shop" is soft-linked to a firm here, not
# a tenant-scoped row)
# --------------------------------------------------------------------------


class FirmTallyShopOut(BaseModel):
    """This firm's tally-agent status. The API key never appears here — it's
    baked into the downloadable installer, not shown to a human."""

    model_config = ConfigDict(from_attributes=True)

    provisioned: bool
    shop_id: str | None = None
    is_active: bool = False
    last_checkin_at: datetime | None = None
    last_upload_at: datetime | None = None
    upload_count: int = 0

    # Seamless-onboarding fields.
    installer_ready: bool = False
    agent_online: bool = False  # checked in within the offline window
    tally_status: str | None = None  # connected | refused | no_company | unknown
    tally_ok_at: datetime | None = None


class FirmBackupFileOut(BaseModel):
    """One file of a backup set. `r2_key` is deliberately not exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    size_bytes: int
    uploaded_at: datetime | None = None


class FirmBackupSetOut(BaseModel):
    """One TallyPrime backup run — a manifest plus one or more data parts,
    all restored together. Download every file into the same folder, then
    use Tally's own Restore."""

    set_id: str | None  # NULL for legacy/standalone single-file uploads
    uploaded_at: datetime | None  # newest file in the set
    total_bytes: int
    files: list[FirmBackupFileOut]


class FirmBackupListOut(BaseModel):
    retention_count: int  # how many backup *sets* are kept before pruning
    sets: list[FirmBackupSetOut]


class FirmTallyShopProvisionResult(BaseModel):
    shop_id: str
    created: bool
    installer_ready: bool = False
    # Deprecated: the key is now delivered inside the installer zip and no
    # longer needs to reach a human. Kept for one release for the emergency
    # "read the raw key" path + existing tests.
    api_key: str = Field(
        default="", description="deprecated — plaintext key, now baked into the installer"
    )

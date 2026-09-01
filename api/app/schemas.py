"""Pydantic request/response models for the auth, tenant, and party APIs.

Kept in one module for M1 — split per-domain when it grows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.models._mixins import (
    AddressType,
    PartyRole,
    PartySource,
    PartyStatus,
    UserRole,
)
from app.reference import (
    LEGAL_NAME_MAX,
    validate_address_line,
    validate_city,
    validate_gstin,
    validate_legal_name,
    validate_pan,
    validate_phone,
    validate_pincode,
    validate_state_code,
)

# Reusable validated string aliases. Each accepts None / "" (-> None) and
# raises a 422 with a helpful message on a malformed value.
Pan = Annotated[str | None, AfterValidator(validate_pan)]
Gstin = Annotated[str | None, AfterValidator(validate_gstin)]
StateCode = Annotated[str | None, AfterValidator(validate_state_code)]
Phone = Annotated[str | None, AfterValidator(validate_phone)]
Pincode = Annotated[str | None, AfterValidator(validate_pincode)]
AddressLine = Annotated[str | None, AfterValidator(validate_address_line)]
City = Annotated[str | None, AfterValidator(validate_city)]
LegalName = Annotated[str, AfterValidator(validate_legal_name)]
OptLegalName = Annotated[str | None, AfterValidator(validate_legal_name)]

# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Bootstrap a firm + its first owner user in one call."""

    firm_name: LegalName
    email: Annotated[EmailStr, Field(max_length=200)]
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    role: UserRole
    tenant_id: str
    # Extension flags the SPA needs at bootstrap (nav gating / route guards).
    ext_inward_import: bool = False


# --------------------------------------------------------------------------
# tenant (the firm)
# --------------------------------------------------------------------------


class TenantUpdate(BaseModel):
    legal_name: OptLegalName = None
    trade_name: str | None = Field(default=None, max_length=LEGAL_NAME_MAX)
    pan: Pan = None
    address: AddressLine = None
    city: City = None
    state_code: StateCode = None
    pincode: Pincode = None
    phone: Phone = None
    email: Annotated[EmailStr | None, Field(max_length=200)] = None
    bank_holder: str | None = Field(default=None, max_length=200)
    bank_name: str | None = Field(default=None, max_length=200)
    bank_ac_no: str | None = Field(default=None, max_length=50)
    bank_ifsc: str | None = Field(default=None, max_length=20)
    bank_branch: str | None = Field(default=None, max_length=200)
    upi_id: str | None = Field(default=None, max_length=100)
    declaration_text: str | None = Field(default=None, max_length=2000)
    terms_text: str | None = Field(default=None, max_length=2000)
    jurisdiction_text: str | None = Field(default=None, max_length=500)
    document_label: str | None = Field(default=None, max_length=50)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    trade_name: str | None
    pan: str | None
    address: str | None
    city: str | None
    state_code: str | None
    pincode: str | None
    phone: str | None
    email: str | None
    bank_holder: str | None
    bank_name: str | None
    bank_ac_no: str | None
    bank_ifsc: str | None
    bank_branch: str | None
    upi_id: str | None
    declaration_text: str | None
    terms_text: str | None
    jurisdiction_text: str | None
    document_label: str
    gst_enabled: bool
    gstin: str | None


# --------------------------------------------------------------------------
# party
# --------------------------------------------------------------------------


class PartyAddressIn(BaseModel):
    type: AddressType = AddressType.both
    line1: AddressLine = None
    line2: AddressLine = None
    line3: AddressLine = None
    city: City = None
    state_code: StateCode = None
    pincode: Pincode = None
    is_default: bool = False


class PartyAddressOut(PartyAddressIn):
    model_config = ConfigDict(from_attributes=True)

    id: str


class PartyBase(BaseModel):
    legal_name: LegalName
    phone: Phone = None
    email: Annotated[EmailStr | None, Field(max_length=200)] = None
    pan: Pan = None
    role: PartyRole = PartyRole.customer
    default_state_code: StateCode = None
    gstin: Gstin = None


class PartyCreate(PartyBase):
    addresses: list[PartyAddressIn] = Field(default_factory=list)


class PartyUpdate(BaseModel):
    legal_name: OptLegalName = None
    phone: Phone = None
    email: Annotated[EmailStr | None, Field(max_length=200)] = None
    pan: Pan = None
    role: PartyRole | None = None
    default_state_code: StateCode = None
    gstin: Gstin = None
    status: PartyStatus | None = None
    addresses: list[PartyAddressIn] | None = None


class PartyCompleteness(BaseModel):
    """Derived, never stored. M1 rule: a party is complete once it has an
    address (line1 + city + state). Advisory only — never gates a document.
    """

    complete: bool
    missing: list[str] = Field(default_factory=list)


class PartyOut(PartyBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: PartyStatus
    source: PartySource
    source_ref: str | None
    last_txn_at: datetime | None
    addresses: list[PartyAddressOut] = Field(default_factory=list)
    completeness: PartyCompleteness
    document_count: int


class PartyListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    role: PartyRole
    phone: str | None
    default_state_code: str | None
    gstin: str | None
    status: PartyStatus
    source: PartySource
    source_ref: str | None
    last_txn_at: datetime | None
    completeness: PartyCompleteness

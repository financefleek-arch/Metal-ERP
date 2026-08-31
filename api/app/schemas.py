"""Pydantic request/response models for the auth, tenant, and party APIs.

Kept in one module for M1 — split per-domain when it grows.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.models._mixins import AddressType, PartyRole, UserRole
from app.reference import validate_gstin, validate_pan, validate_state_code

# Reusable validated string aliases. Each accepts None / "" (-> None) and
# raises a 422 with a helpful message on a malformed value.
Pan = Annotated[str | None, AfterValidator(validate_pan)]
Gstin = Annotated[str | None, AfterValidator(validate_gstin)]
StateCode = Annotated[str | None, AfterValidator(validate_state_code)]

# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Bootstrap a firm + its first owner user in one call."""

    firm_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
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


# --------------------------------------------------------------------------
# tenant (the firm)
# --------------------------------------------------------------------------


class TenantUpdate(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    trade_name: str | None = Field(default=None, max_length=200)
    pan: Pan = None
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    state_code: StateCode = None
    pincode: str | None = Field(default=None, max_length=6)
    phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    bank_holder: str | None = Field(default=None, max_length=200)
    bank_name: str | None = Field(default=None, max_length=200)
    bank_ac_no: str | None = Field(default=None, max_length=50)
    bank_ifsc: str | None = Field(default=None, max_length=20)
    bank_branch: str | None = Field(default=None, max_length=200)
    upi_id: str | None = Field(default=None, max_length=100)
    declaration_text: str | None = None
    terms_text: str | None = None
    jurisdiction_text: str | None = None
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
    line1: str | None = Field(default=None, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    line3: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state_code: StateCode = None
    pincode: str | None = Field(default=None, max_length=6)
    is_default: bool = False


class PartyAddressOut(PartyAddressIn):
    model_config = ConfigDict(from_attributes=True)

    id: str


class PartyBase(BaseModel):
    legal_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    pan: Pan = None
    role: PartyRole = PartyRole.customer
    default_state_code: StateCode = None
    gstin: Gstin = None


class PartyCreate(PartyBase):
    addresses: list[PartyAddressIn] = Field(default_factory=list)


class PartyUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    pan: Pan = None
    role: PartyRole | None = None
    default_state_code: StateCode = None
    gstin: Gstin = None
    addresses: list[PartyAddressIn] | None = None


class PartyOut(PartyBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    addresses: list[PartyAddressOut] = Field(default_factory=list)


class PartyListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    legal_name: str
    role: PartyRole
    phone: str | None
    default_state_code: str | None
    gstin: str | None

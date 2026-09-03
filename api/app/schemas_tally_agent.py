"""Pydantic I/O for the Tally companion agent API (`/api/tally-agent/*`)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OutboxItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    module: str
    payload: dict


class ShopCheckinIn(BaseModel):
    # Per-module status this poll, e.g. {"backup": "ok", "whatsapp_delivery": "tally_not_open"}.
    module_status: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class ShopCheckinOut(BaseModel):
    shop_id: str
    checked_in_at: datetime
    outbox: list[OutboxItemOut] = Field(default_factory=list)


class UploadRequestIn(BaseModel):
    filename: str
    size_bytes: int = Field(ge=0)


class UploadRequestOut(BaseModel):
    upload_id: str
    put_url: str
    r2_key: str
    expires_in: int


class UploadConfirmIn(BaseModel):
    upload_id: str
    status: str = Field(default="confirmed", pattern="^(confirmed|failed)$")


class UploadConfirmOut(BaseModel):
    upload_id: str
    status: str


class ShopStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    is_active: bool
    tenant_id: str | None
    last_checkin_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    last_upload_at: datetime | None = None
    upload_count: int = 0

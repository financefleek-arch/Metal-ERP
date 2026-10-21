"""The firm's licence as last reported by the Fleek control plane (docs/PLAN-fleek-control-plane.md 13.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class TenantEntitlement(PkUuidMixin, TimestampMixin, Base):
    """One row per linked firm: the last good answer from Fleek and when we may rely on it.

    A firm with no `tenant.fleek_org_id` has no row and is unlimited, exactly as before it was linked."""

    __tablename__ = "tenant_entitlement"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, unique=True, index=True)
    # The control plane's answer, verbatim (version, status, plan, limits, modules, ...). No billing detail is
    # ever in it.
    payload: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    # When it was fetched and until when it may be relied on without asking again (Fleek's `valid_until`).
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The latest refresh attempt that did not succeed, and when we last tried (so a down Fleek is not asked on
    # every request).
    last_error: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Short label for admin screens.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")

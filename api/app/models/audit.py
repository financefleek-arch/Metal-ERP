"""Append-only audit log for state transitions and field changes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

# JSONB on Postgres (indexable, typed), plain JSON on SQLite (tests/CI).
_JSON = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "audit_log"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    entity: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON)

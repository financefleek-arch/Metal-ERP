"""What a person does after sign-in, as keys and counts only (docs/PLAN-shop-activity-intelligence.md).

One row per event. Never a name, an amount, a phone number or a message: `props` holds at most a few short
labels such as which Setup step was tapped (see `services/activity.sanitize`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import PkUuidMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class ActivityEvent(PkUuidMixin, Base):
    __tablename__ = "activity_event"
    __table_args__ = (Index("ix_activity_tenant_key_at", "tenant_id", "key", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    props: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

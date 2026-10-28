"""Slip-capture usage: one row per photo sent for reading, so the cost can be predicted."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class SlipCapture(PkUuidMixin, TimestampMixin, Base):
    """Tokens are the real counts from the API response, not an estimate. Cost is worked out
    from them when read (`services/ocr/slip_usage.py`), so a price change never rewrites history."""

    __tablename__ = "slip_capture"
    __table_args__ = (Index("ix_slip_capture_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    ok: Mapped[bool] = mapped_column(nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    image_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(300))

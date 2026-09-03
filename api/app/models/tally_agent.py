"""Tally companion agent — shop registry, backup uploads, outbox.

`BackupShop` is deliberately **not** tenant-scoped: a "shop" here is an
install of the Windows tally-agent tool, a different sold product from
Metal ERP itself. A shop may optionally be soft-linked to a Metal ERP
tenant (`tenant_id`) when the same customer runs both, but that link is
informational only — nothing in this feature requires it.

`api_key_hash` never stores the plaintext key; `tools.make_backup_shop`
prints it exactly once at creation.

`BackupUpload` is one row per backup file shipped to cloud storage —
`status` moves pending -> confirmed (or failed, left for a retry) as the
agent completes the direct-to-R2 PUT.

`AgentOutboxItem` is a generic per-shop, per-module queue: any
Gateway-dependent module (e.g. WhatsApp delivery for Tally-only shops)
enqueues work here: the agent drains its module's queued rows on each
checkin, so the backend doesn't need a bespoke table per module.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class BackupShop(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "backup_shop"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional soft link — informational only, see module docstring.
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenant.id"), index=True)

    last_checkin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupUpload(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "backup_upload"

    shop_id: Mapped[str] = mapped_column(ForeignKey("backup_shop.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    r2_key: Mapped[str] = mapped_column(String(500), nullable=False)

    # pending | confirmed | failed
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False, index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentOutboxItem(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "agent_outbox_item"

    shop_id: Mapped[str] = mapped_column(ForeignKey("backup_shop.id"), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # queued | sent | failed
    status: Mapped[str] = mapped_column(String(12), default="queued", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

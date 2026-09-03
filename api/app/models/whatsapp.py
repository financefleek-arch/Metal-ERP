"""WhatsApp Business (Meta Cloud API) — per-tenant number registry + send log.

We reuse the "FleekWA" Meta app exactly as fleek-backend does: one
process-wide System User token (`whatsapp_api_key`) plus the App Secret,
both env values (see app.config). This table only records *which number a
firm sends from* — `phone_number_id` selects the number on
`POST /{phone_number_id}/messages`; the token is app-wide, not per-firm, so
there is nothing secret to store here. (If a firm ever moves its WABA into
its own Business Manager, a nullable per-firm token column can be added
then — a one-line migration.)

`WhatsappMessage` is the audit trail: one row per outbound message, moved
through pending -> sent -> delivered -> read (or failed) by the send call and
the status webhook.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class TenantWhatsappConfig(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "tenant_whatsapp_config"

    # One config row per firm. Unique (not the PK) so the row keeps a stable
    # id across credential edits.
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id"), nullable=False, unique=True, index=True
    )

    # Meta identifiers for this firm's WhatsApp Business number.
    phone_number_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    waba_id: Mapped[str] = mapped_column(String(40), nullable=False)
    # Human-friendly, for the admin UI only (e.g. "+91 98xxxxxx02").
    display_phone_number: Mapped[str | None] = mapped_column(String(30))

    # Off => the firm's send routes 422 and the webhook ignores its statuses.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WhatsappMessage(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "whatsapp_message"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"), index=True)
    # Set when the message is about a specific invoice (invoice_ready /
    # payment_reminder). Deep-links the log row back to the document.
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoice.id"), index=True)

    template_name: Mapped[str] = mapped_column(String(80), nullable=False)
    to_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    # Meta media id for the attached PDF, if any (from the /media upload).
    media_id: Mapped[str | None] = mapped_column(String(80))

    # Meta's wamid, returned by POST /messages. Null only if the send call
    # itself failed before Meta accepted it.
    wa_message_id: Mapped[str | None] = mapped_column(String(80), index=True)

    # pending | sent | delivered | read | failed
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

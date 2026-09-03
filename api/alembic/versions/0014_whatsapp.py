"""WhatsApp Business integration: per-tenant number registry, party opt-in, log.

`tenant_whatsapp_config` — one row per firm: the Meta phone_number_id/waba_id
that selects which number the firm sends from. The System User token and App
Secret are process-wide env values (we reuse the "FleekWA" Meta app, whose
one app-wide token already covers every number), so there is deliberately no
token column here.

`party.whatsapp_optin` — a send is refused unless this is true and the party
has a phone.

`whatsapp_message` — outbound audit trail, pending -> sent -> delivered ->
read (or failed), advanced by the send call and the status webhook.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_whatsapp_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("phone_number_id", sa.String(length=40), nullable=False),
        sa.Column("waba_id", sa.String(length=40), nullable=False),
        sa.Column("display_phone_number", sa.String(length=30), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_whatsapp_config_tenant"),
    )
    op.create_index(
        "ix_tenant_whatsapp_config_tenant_id", "tenant_whatsapp_config", ["tenant_id"]
    )
    op.create_index(
        "ix_tenant_whatsapp_config_phone_number_id",
        "tenant_whatsapp_config",
        ["phone_number_id"],
    )

    op.add_column(
        "party",
        sa.Column(
            "whatsapp_optin", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

    op.create_table(
        "whatsapp_message",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("party_id", sa.String(length=36), nullable=True),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("template_name", sa.String(length=80), nullable=False),
        sa.Column("to_phone", sa.String(length=20), nullable=False),
        sa.Column("media_id", sa.String(length=80), nullable=True),
        sa.Column("wa_message_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.ForeignKeyConstraint(["party_id"], ["party.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoice.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_message_tenant_id", "whatsapp_message", ["tenant_id"])
    op.create_index("ix_whatsapp_message_party_id", "whatsapp_message", ["party_id"])
    op.create_index("ix_whatsapp_message_invoice_id", "whatsapp_message", ["invoice_id"])
    op.create_index("ix_whatsapp_message_wa_message_id", "whatsapp_message", ["wa_message_id"])
    op.create_index("ix_whatsapp_message_status", "whatsapp_message", ["status"])


def downgrade() -> None:
    op.drop_table("whatsapp_message")
    op.drop_column("party", "whatsapp_optin")
    op.drop_index(
        "ix_tenant_whatsapp_config_phone_number_id", table_name="tenant_whatsapp_config"
    )
    op.drop_index(
        "ix_tenant_whatsapp_config_tenant_id", table_name="tenant_whatsapp_config"
    )
    op.drop_table("tenant_whatsapp_config")

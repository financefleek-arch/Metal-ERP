"""Tally companion agent: shop registry, backup uploads, module outbox.

`backup_shop` is the first table in this schema with no required
`tenant_id` — a "shop" is an install-level record for the separate Tally
companion agent product (cloud backup sync + future Gateway-based
modules), not a Metal ERP accounting tenant. `tenant_id` is an optional
soft link for shops that are also Metal ERP customers.

`backup_upload` is one row per backup file shipped to R2. `agent_outbox_item`
is a generic per-shop, per-module work queue so a future Gateway-dependent
module (e.g. WhatsApp delivery for Tally-only shops) doesn't need its own
table.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_shop",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("api_key_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("last_checkin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_hash", name="uq_backup_shop_api_key_hash"),
    )
    op.create_index("ix_backup_shop_tenant_id", "backup_shop", ["tenant_id"])

    op.create_table(
        "backup_upload",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("shop_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("r2_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["backup_shop.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_upload_shop_id", "backup_upload", ["shop_id"])
    op.create_index("ix_backup_upload_status", "backup_upload", ["status"])

    op.create_table(
        "agent_outbox_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("shop_id", sa.String(length=36), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["backup_shop.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_outbox_item_shop_id", "agent_outbox_item", ["shop_id"])
    op.create_index("ix_agent_outbox_item_module", "agent_outbox_item", ["module"])
    op.create_index("ix_agent_outbox_item_status", "agent_outbox_item", ["status"])


def downgrade() -> None:
    op.drop_table("agent_outbox_item")
    op.drop_index("ix_backup_upload_status", table_name="backup_upload")
    op.drop_index("ix_backup_upload_shop_id", table_name="backup_upload")
    op.drop_table("backup_upload")
    op.drop_index("ix_backup_shop_tenant_id", table_name="backup_shop")
    op.drop_table("backup_shop")

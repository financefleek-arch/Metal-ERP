"""Supplier Catalog S4: versioned customer catalog PDFs.

Adds `customer_catalog`: one row per generated customer-facing catalog PDF (a snapshot with
a version number and a `stale` flag set when prices or items change afterwards).

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "customer_catalog",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "catalog_id", sa.String(36), sa.ForeignKey("supplier_catalog.id"), nullable=False
        ),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("options_json", _JSON),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("pdf_key", sa.String(300), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("catalog_id", "version", name="uq_customer_catalog_version"),
    )
    op.create_index("ix_customer_catalog_tenant_id", "customer_catalog", ["tenant_id"])
    op.create_index("ix_customer_catalog_catalog_id", "customer_catalog", ["catalog_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_catalog_catalog_id", table_name="customer_catalog")
    op.drop_index("ix_customer_catalog_tenant_id", table_name="customer_catalog")
    op.drop_table("customer_catalog")

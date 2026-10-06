"""Customer catalogs are built from items, not from a supplier catalog.

Not in production, so the old table (one per supplier catalog, flagged stale by edits) is dropped
and recreated firm-level: a series of versions, each with the selection it came from and the
items it printed. Files already stored for the old rows are orphaned and harmless.

  - customer_catalog.series_id        versions of one catalog share it
  - customer_catalog.selection_json   {"ids": [...]} or {"filter": {...}}: what a rebuild repeats
  - customer_catalog.member_ids_json  the items printed, for the out-of-date check
  - catalog_output_job.catalog_id     now nullable (firm-level jobs have no supplier catalog)

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.drop_index("ix_customer_catalog_catalog_id", table_name="customer_catalog")
    op.drop_index("ix_customer_catalog_tenant_id", table_name="customer_catalog")
    op.drop_table("customer_catalog")
    op.create_table(
        "customer_catalog",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("series_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("options_json", _JSON),
        sa.Column("selection_json", _JSON),
        sa.Column("member_ids_json", _JSON),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("pdf_key", sa.String(300), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "series_id", "version", name="uq_customer_catalog_version"),
    )
    op.create_index("ix_customer_catalog_tenant_id", "customer_catalog", ["tenant_id"])
    op.create_index("ix_customer_catalog_series_id", "customer_catalog", ["series_id"])
    op.alter_column("catalog_output_job", "catalog_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM catalog_output_job WHERE catalog_id IS NULL")
    op.alter_column("catalog_output_job", "catalog_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_customer_catalog_series_id", table_name="customer_catalog")
    op.drop_index("ix_customer_catalog_tenant_id", table_name="customer_catalog")
    op.drop_table("customer_catalog")
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

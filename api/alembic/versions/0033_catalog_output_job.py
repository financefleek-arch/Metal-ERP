"""Supplier Catalog S3: background output jobs (large label runs).

Adds `catalog_output_job`: one row per generated file that is too big to build
inside a request. The customer-catalog table arrives with S4 in its own migration.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "catalog_output_job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "catalog_id", sa.String(36), sa.ForeignKey("supplier_catalog.id"), nullable=False
        ),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("params_json", _JSON),
        sa.Column("status", sa.String(10), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer()),
        sa.Column("result_key", sa.String(300)),
        sa.Column("scan_warning", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_catalog_output_job_tenant_id", "catalog_output_job", ["tenant_id"])
    op.create_index("ix_catalog_output_job_catalog_id", "catalog_output_job", ["catalog_id"])


def downgrade() -> None:
    op.drop_index("ix_catalog_output_job_catalog_id", table_name="catalog_output_job")
    op.drop_index("ix_catalog_output_job_tenant_id", table_name="catalog_output_job")
    op.drop_table("catalog_output_job")

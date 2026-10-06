"""Supplier catalog defaults: margin, rounding, group map, add automatically, mark in stock.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_catalog_default",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=False),
        sa.Column("bulk_margin_pct", sa.Numeric(7, 2), nullable=True),
        sa.Column("rounding_step", sa.Integer(), nullable=True),
        sa.Column(
            "group_map",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("add_automatically", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mark_in_stock", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "party_id", name="uq_supplier_catalog_default_party"),
    )
    op.create_index(
        "ix_supplier_catalog_default_tenant_id", "supplier_catalog_default", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_catalog_default_tenant_id", table_name="supplier_catalog_default")
    op.drop_table("supplier_catalog_default")

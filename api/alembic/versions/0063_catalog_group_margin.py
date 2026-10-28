"""Per-group margin on a supplier price list (item margin > group margin > bulk margin).

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_catalog_group_margin",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "catalog_id", sa.String(36), sa.ForeignKey("supplier_catalog.id"), nullable=False
        ),
        sa.Column(
            "category_id", sa.String(36), sa.ForeignKey("item_category.id"), nullable=False
        ),
        sa.Column("margin_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("catalog_id", "category_id", name="uq_catalog_group_margin"),
    )
    op.create_index(
        "ix_supplier_catalog_group_margin_tenant_id", "supplier_catalog_group_margin", ["tenant_id"]
    )
    op.create_index(
        "ix_supplier_catalog_group_margin_catalog_id", "supplier_catalog_group_margin", ["catalog_id"]
    )
    op.create_index(
        "ix_supplier_catalog_group_margin_category_id", "supplier_catalog_group_margin", ["category_id"]
    )


def downgrade() -> None:
    op.drop_table("supplier_catalog_group_margin")

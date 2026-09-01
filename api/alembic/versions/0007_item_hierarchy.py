"""item hierarchy: per-tenant item_category, product_group surfacing,
item.rate_mode / weight_per_piece / category_id, item_alias → group + source.

Phase 1 of the catalogue-learning plan. Loop 1/2 columns on inward_bill_line
land with those slices; this migration is the substrate + the parser's needs.

The legacy `item.category` / `product_group.category` strings are KEPT
(category_id is authoritative once set; the string is a fallback until
Loop 1/2 fully cut over).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_category",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("sort", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_item_category_tenant_name"),
    )
    op.create_index("ix_item_category_tenant_id", "item_category", ["tenant_id"])

    # --- product_group ---
    op.add_column(
        "product_group",
        sa.Column("name_normalized", sa.String(200), server_default="", nullable=False),
    )
    op.add_column(
        "product_group",
        sa.Column("category_id", sa.String(36), sa.ForeignKey("item_category.id")),
    )
    op.add_column(
        "product_group",
        sa.Column(
            "default_rate_mode", sa.String(10), server_default="piece", nullable=False
        ),
    )
    op.create_index("ix_product_group_category_id", "product_group", ["category_id"])
    op.create_unique_constraint(
        "uq_group_tenant_normname", "product_group", ["tenant_id", "name_normalized"]
    )

    # --- item ---
    op.add_column(
        "item", sa.Column("category_id", sa.String(36), sa.ForeignKey("item_category.id"))
    )
    op.add_column(
        "item", sa.Column("rate_mode", sa.String(10), server_default="piece", nullable=False)
    )
    op.add_column("item", sa.Column("weight_per_piece", sa.Numeric(12, 3)))
    op.create_index("ix_item_category_id", "item", ["category_id"])

    # --- item_alias: allow pointing at a group; carry source + last_used_at ---
    with op.batch_alter_table("item_alias") as batch:
        batch.alter_column("item_id", existing_type=sa.String(36), nullable=True)
    op.add_column(
        "item_alias", sa.Column("group_id", sa.String(36), sa.ForeignKey("product_group.id"))
    )
    op.add_column(
        "item_alias",
        sa.Column("source", sa.String(20), server_default="manual", nullable=False),
    )
    op.add_column(
        "item_alias", sa.Column("last_used_at", sa.DateTime(timezone=True))
    )
    op.create_index("ix_item_alias_group_id", "item_alias", ["group_id"])

    # Backfill name_normalized on any pre-existing groups (there are none in M1,
    # but keep the migration honest): lowercase the name.
    op.execute("UPDATE product_group SET name_normalized = lower(name) WHERE name_normalized = ''")


def downgrade() -> None:
    op.drop_index("ix_item_alias_group_id", table_name="item_alias")
    op.drop_column("item_alias", "last_used_at")
    op.drop_column("item_alias", "source")
    op.drop_column("item_alias", "group_id")
    with op.batch_alter_table("item_alias") as batch:
        batch.alter_column("item_id", existing_type=sa.String(36), nullable=False)

    op.drop_index("ix_item_category_id", table_name="item")
    op.drop_column("item", "weight_per_piece")
    op.drop_column("item", "rate_mode")
    op.drop_column("item", "category_id")

    op.drop_constraint("uq_group_tenant_normname", "product_group", type_="unique")
    op.drop_index("ix_product_group_category_id", table_name="product_group")
    op.drop_column("product_group", "default_rate_mode")
    op.drop_column("product_group", "category_id")
    op.drop_column("product_group", "name_normalized")

    op.drop_index("ix_item_category_tenant_id", table_name="item_category")
    op.drop_table("item_category")

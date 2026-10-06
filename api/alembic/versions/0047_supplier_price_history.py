"""Supplier price history, and the flags a bill line gets from it.

  - supplier_price_point        what a supplier quoted (price list) or charged (bill), dated
  - inward_bill_line.note_flag  above_quote | discontinued (a second look at review)
  - inward_bill_line.quoted_rate  the latest quote the flag compared against

Existing price lists get one quote point each (their cost, dated by their import day), so the
first re-import after this already shows what changed.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_price_point",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("supplier_party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("catalog_product.id"), nullable=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id"), nullable=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("price", sa.Numeric(15, 2), nullable=False),
        sa.Column("pack_qty", sa.Integer(), nullable=True),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_supplier_price_point_tenant_id", "supplier_price_point", ["tenant_id"])
    op.create_index(
        "ix_price_point_product",
        "supplier_price_point",
        ["tenant_id", "product_id", "source", "on_date"],
    )
    op.create_index(
        "ix_price_point_item",
        "supplier_price_point",
        ["tenant_id", "item_id", "source", "on_date"],
    )
    op.add_column("inward_bill_line", sa.Column("note_flag", sa.String(16), nullable=True))
    op.add_column("inward_bill_line", sa.Column("quoted_rate", sa.Numeric(15, 2), nullable=True))
    op.execute(
        """
        INSERT INTO supplier_price_point
            (id, tenant_id, supplier_party_id, product_id, source, source_id, price, pack_qty,
             on_date)
        SELECT gen_random_uuid()::text, i.tenant_id, c.supplier_party_id, i.product_id, 'quote',
               c.id, i.cost_price, i.pack_qty, c.created_at::date
          FROM supplier_catalog_item i
          JOIN supplier_catalog c ON c.id = i.catalog_id
         WHERE i.product_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("inward_bill_line", "quoted_rate")
    op.drop_column("inward_bill_line", "note_flag")
    op.drop_index("ix_price_point_item", table_name="supplier_price_point")
    op.drop_index("ix_price_point_product", table_name="supplier_price_point")
    op.drop_index("ix_supplier_price_point_tenant_id", table_name="supplier_price_point")
    op.drop_table("supplier_price_point")

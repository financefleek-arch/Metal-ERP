"""Supplier Catalog: price by margin %, not by multiplier.

`supplier_catalog.multiplier` (e.g. 1.250) becomes `bulk_margin_pct` (25.00) and
`supplier_catalog_item.multiplier_override` becomes `item_margin_pct`. Existing values are
converted (margin = (multiplier - 1) x 100), so every stored price stays exactly as it was.
Sell prices themselves are untouched.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "supplier_catalog",
        sa.Column("bulk_margin_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
    )
    op.execute("UPDATE supplier_catalog SET bulk_margin_pct = ROUND((multiplier - 1) * 100, 2)")
    op.drop_column("supplier_catalog", "multiplier")

    op.add_column(
        "supplier_catalog_item", sa.Column("item_margin_pct", sa.Numeric(7, 2), nullable=True)
    )
    op.execute(
        "UPDATE supplier_catalog_item "
        "SET item_margin_pct = ROUND((multiplier_override - 1) * 100, 2) "
        "WHERE multiplier_override IS NOT NULL"
    )
    op.drop_column("supplier_catalog_item", "multiplier_override")


def downgrade() -> None:
    op.add_column(
        "supplier_catalog_item", sa.Column("multiplier_override", sa.Numeric(6, 3), nullable=True)
    )
    op.execute(
        "UPDATE supplier_catalog_item "
        "SET multiplier_override = ROUND(1 + item_margin_pct / 100, 3) "
        "WHERE item_margin_pct IS NOT NULL"
    )
    op.drop_column("supplier_catalog_item", "item_margin_pct")

    op.add_column(
        "supplier_catalog",
        sa.Column("multiplier", sa.Numeric(6, 3), nullable=False, server_default="1.000"),
    )
    op.execute("UPDATE supplier_catalog SET multiplier = ROUND(1 + bulk_margin_pct / 100, 3)")
    op.drop_column("supplier_catalog", "bulk_margin_pct")

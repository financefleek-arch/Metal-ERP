"""Items: availability status and pack / carton size.

  - item.availability   in_stock | expected | out_of_stock | discontinued. A status, not a
                        quantity. Existing items are In stock (they are the firm's own items).
  - item.pack_qty       pieces in one sale unit, for "for 7 pcs" and price per piece
  - item.carton_qty     pieces in a carton

Existing catalog-promoted items get their pack and carton size from the catalog row they came
from. Availability of existing items is left In stock.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("availability", sa.String(12), nullable=False, server_default="in_stock"),
    )
    op.create_index("ix_item_availability", "item", ["availability"])
    op.add_column("item", sa.Column("pack_qty", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("carton_qty", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE item
           SET pack_qty = r.pack_qty, carton_qty = r.carton_qty
          FROM (
                SELECT DISTINCT ON (item_id) item_id, pack_qty, carton_qty
                  FROM supplier_catalog_item
                 WHERE item_id IS NOT NULL
                 ORDER BY item_id, created_at DESC
               ) r
         WHERE item.id = r.item_id
        """
    )


def downgrade() -> None:
    op.drop_column("item", "carton_qty")
    op.drop_column("item", "pack_qty")
    op.drop_index("ix_item_availability", table_name="item")
    op.drop_column("item", "availability")

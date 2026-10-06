"""Items: Tally push state lives on the item.

  - item.tally_status       none | queued | synced | error
  - item.tally_price        selling price last sent to Tally
  - item.tally_pushed_at    when it was last sent
  - item.tally_seen_price   what Tally showed at the last check (0 = no price there)

Items already pushed from a supplier catalog (a synced catalog row) take that row's state.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("tally_status", sa.String(8), nullable=False, server_default="none"),
    )
    op.add_column("item", sa.Column("tally_price", sa.Numeric(15, 2), nullable=True))
    op.add_column("item", sa.Column("tally_pushed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("item", sa.Column("tally_seen_price", sa.Numeric(15, 2), nullable=True))
    op.execute(
        """
        UPDATE item
           SET tally_status = 'synced',
               tally_price = r.tally_price,
               tally_pushed_at = r.tally_pushed_at
          FROM (
                SELECT DISTINCT ON (item_id) item_id, tally_price, tally_pushed_at
                  FROM supplier_catalog_item
                 WHERE item_id IS NOT NULL AND tally_status = 'synced'
                 ORDER BY item_id, tally_pushed_at DESC NULLS LAST
               ) r
         WHERE item.id = r.item_id
        """
    )


def downgrade() -> None:
    op.drop_column("item", "tally_seen_price")
    op.drop_column("item", "tally_pushed_at")
    op.drop_column("item", "tally_price")
    op.drop_column("item", "tally_status")

"""Supplier Catalog: remember the selling price last sent to Tally.

`supplier_catalog_item.tally_price` is the selling price last pushed to Tally for the row. When
the current price differs (a margin change, a re-price), the row is offered for a price update,
and a row synced before this existed (NULL) is updated once so Tally gets its price.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "supplier_catalog_item", sa.Column("tally_price", sa.Numeric(15, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("supplier_catalog_item", "tally_price")

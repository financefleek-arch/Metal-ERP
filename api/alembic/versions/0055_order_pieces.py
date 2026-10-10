"""Customer ordering: loose pieces, chosen per catalog link.

  - catalog_share_link.allow_pieces         customers may order single pieces (off by default)
  - catalog_share_link.piece_premium_pct    extra on the per-piece price (0 = pack price / pieces)
  - catalog_share_link.price_basis          which price the page shows large: 'pack' or 'piece'
  - customer_order_line.unit                what qty counts: 'pack' (the default) or 'piece'

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_share_link",
        sa.Column("allow_pieces", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "catalog_share_link",
        sa.Column("piece_premium_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "catalog_share_link",
        sa.Column("price_basis", sa.String(5), nullable=False, server_default="pack"),
    )
    op.add_column(
        "customer_order_line",
        sa.Column("unit", sa.String(5), nullable=False, server_default="pack"),
    )


def downgrade() -> None:
    op.drop_column("customer_order_line", "unit")
    op.drop_column("catalog_share_link", "price_basis")
    op.drop_column("catalog_share_link", "piece_premium_pct")
    op.drop_column("catalog_share_link", "allow_pieces")

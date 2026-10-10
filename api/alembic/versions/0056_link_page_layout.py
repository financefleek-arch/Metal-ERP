"""Customer catalog page: how each link lays out its items.

  - catalog_share_link.columns         items per row on a phone: 1, 2 (the default) or 3
  - catalog_share_link.show_code       show the item code on each card
  - catalog_share_link.show_pack_info  show "for 6 pcs" and the per-piece price on each card

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_share_link",
        sa.Column("columns", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "catalog_share_link",
        sa.Column("show_code", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "catalog_share_link",
        sa.Column("show_pack_info", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("catalog_share_link", "show_pack_info")
    op.drop_column("catalog_share_link", "show_code")
    op.drop_column("catalog_share_link", "columns")

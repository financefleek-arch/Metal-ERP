"""Share links: an opt-in "leave out items marked Out of stock" (replaces include_expected).

A price list says nothing about stock, so availability no longer decides what a catalog, link or order
shows. The old `include_expected` and `supplier_catalog_default.mark_in_stock` columns stay but are no
longer read.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_share_link") as b:
        b.add_column(
            sa.Column("leave_out_of_stock", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("catalog_share_link") as b:
        b.drop_column("leave_out_of_stock")

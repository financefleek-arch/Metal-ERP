"""Supplier Catalog S5: settings + last-seen stock masters for the Tally item push.

On `tally_company`:
  - stock_group_root           Tally stock group our new groups are created under (NULL = Primary)
  - tally_group_create_policy  'create_missing' | 'existing_only'
  - stock_group_map            {item_category_id: Tally stock group name} overrides
  - known_stock_groups         [{name, parent}] from the last "check Tally" (never guessed)
  - known_stock_items          [lower-case names] from the last "check Tally"
  - known_stock_at             when that check ran

All nullable or defaulted: no backfill, nothing changes for firms that never push items.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("tally_company", sa.Column("stock_group_root", sa.String(200), nullable=True))
    op.add_column(
        "tally_company",
        sa.Column(
            "tally_group_create_policy",
            sa.String(20),
            nullable=False,
            server_default="create_missing",
        ),
    )
    op.add_column(
        "tally_company",
        sa.Column("stock_group_map", _JSON, nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("tally_company", sa.Column("known_stock_groups", _JSON, nullable=True))
    op.add_column("tally_company", sa.Column("known_stock_items", _JSON, nullable=True))
    op.add_column(
        "tally_company", sa.Column("known_stock_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    for col in (
        "known_stock_at",
        "known_stock_items",
        "known_stock_groups",
        "stock_group_map",
        "tally_group_create_policy",
        "stock_group_root",
    ):
        op.drop_column("tally_company", col)

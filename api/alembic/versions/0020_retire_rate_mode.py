"""Retire rate_mode / default_rate_mode.

`rate_mode` (piece | kg) was a normalised weight-vs-piece flag that sat
alongside the free-text `uom` string. Now that `uom` is drawn from a
canonical table (`shared/units.json`) with an `is_weight_uom()` helper,
`rate_mode` carries no information `uom` doesn't — it was a derived shadow
that could drift from its source. Every consumer now keys off `uom`.

Drops:
  item.rate_mode
  product_group.default_rate_mode
  staging_tally_item.proposed_rate_mode   (transient import-staging table)

Kept: item.weight_per_piece — still used by the inward pooled-line splitter
(kg per one piece, for a weight-priced item).

Downgrade re-adds the columns as NOT NULL DEFAULT 'piece'; the original
per-row values are not recoverable (and weren't meaningful — 'piece' was
the default for ~everything).

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    ("item", "rate_mode"),
    ("product_group", "default_rate_mode"),
    ("staging_tally_item", "proposed_rate_mode"),
)


def upgrade() -> None:
    for table, col in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(col)


def downgrade() -> None:
    for table, col in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    col,
                    sa.String(length=10),
                    nullable=False,
                    server_default="piece",
                )
            )

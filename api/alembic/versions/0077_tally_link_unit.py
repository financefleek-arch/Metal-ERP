"""An item's Tally unit is kept per Tally company (S1.2b).

`tally_link.tally_unit` holds the unit a company has for an item. Backfill: each item link takes the
unit the item carried (`item.tally_unit`), since a firm had one company until 0075. The old column stays,
unread, as a fallback for an item with no link in any company.

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tally_link", sa.Column("tally_unit", sa.String(40), nullable=True))
    op.execute(
        "UPDATE tally_link SET tally_unit = ("
        "SELECT i.tally_unit FROM item i WHERE i.id = tally_link.entity_id) "
        "WHERE entity_type = 'item'"
    )


def downgrade() -> None:
    op.drop_column("tally_link", "tally_unit")

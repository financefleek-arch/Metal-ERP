"""Items keep the unit Tally holds for them.

A voucher line in any other unit posts to Tally with a blank quantity and rate (verified live), so
each Tally-linked item records Tally's own unit name. Additive: one nullable column, filled where we
already hold the answer (the staged masters rows an item was committed from). Everything else stays
empty until the next masters pull or Check Tally, and a push for such an item is held back until then.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("item", sa.Column("tally_unit", sa.String(40)))
    op.execute(
        """
        UPDATE item SET tally_unit = (
            SELECT s.base_units FROM staging_tally_item s
            WHERE s.committed_as = item.id AND s.base_units IS NOT NULL AND s.base_units <> ''
            ORDER BY s.created_at DESC LIMIT 1
        )
        WHERE tally_guid IS NOT NULL AND tally_unit IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("item", "tally_unit")

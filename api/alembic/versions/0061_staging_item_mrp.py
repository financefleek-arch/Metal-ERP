"""Stage the Tally stock item's MRP.

  - staging_tally_item.mrp   the item's MRP from Tally (MRPRATE). Becomes `item.mrp` on commit.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("staging_tally_item", sa.Column("mrp", sa.Numeric(15, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("staging_tally_item", "mrp")

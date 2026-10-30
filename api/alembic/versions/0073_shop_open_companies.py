"""Remember which Tally companies the agent saw open at its last checkin.

The panel can then offer "pick the open company", and each linked company's verify state stays current
without anyone pressing Verify. Additive: two nullable columns.

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backup_shop",
        sa.Column(
            "tally_companies",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "backup_shop", sa.Column("tally_companies_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("backup_shop", "tally_companies_at")
    op.drop_column("backup_shop", "tally_companies")

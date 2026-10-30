"""A push that ended without Tally answering is marked, so a retry checks Tally first.

Tally does not reject a duplicate voucher (verified live), so a retry after a push with an unknown
outcome must look before it posts. Additive: one boolean, false for every existing row.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tally_sync_job",
        sa.Column("outcome_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tally_sync_job", "outcome_unknown")

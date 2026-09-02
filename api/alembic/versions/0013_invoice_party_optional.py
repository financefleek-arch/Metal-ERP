"""Make invoice.party_id nullable so a draft can be saved before a party
is chosen.

The finalize gate (`finalize_blockers`) still requires a party, so a
numbered invoice never lacks one — this only lets an in-progress draft
persist its lines while the party is still blank.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "invoice",
        "party_id",
        existing_type=sa.String(36),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "invoice",
        "party_id",
        existing_type=sa.String(36),
        nullable=False,
    )

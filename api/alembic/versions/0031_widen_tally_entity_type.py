"""F5d fix: widen tally entity_type columns to fit 'inward_bill'.

`tally_sync_job.entity_type` and `tally_link.entity_type` were created as
VARCHAR(10) in `0027` (`0022` for tally_link) - sized for 'invoice' /
'party' / 'item'. F5d's purchase push writes 'inward_bill' (11 chars),
which overflowed with a Postgres StringDataRightTruncation and poisoned
the approve transaction. Widen both to VARCHAR(20).

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tally_sync_job",
        "entity_type",
        existing_type=sa.String(10),
        type_=sa.String(20),
        existing_nullable=True,
    )
    op.alter_column(
        "tally_link",
        "entity_type",
        existing_type=sa.String(10),
        type_=sa.String(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "tally_link",
        "entity_type",
        existing_type=sa.String(20),
        type_=sa.String(10),
        existing_nullable=False,
    )
    op.alter_column(
        "tally_sync_job",
        "entity_type",
        existing_type=sa.String(20),
        type_=sa.String(10),
        existing_nullable=True,
    )

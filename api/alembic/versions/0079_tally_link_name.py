"""A link remembers the master's name in its Tally company (S1.3).

`tally_link.tally_name` is what a push names the master by, so renaming a customer or item here does not
orphan its vouchers. Backfill: each party and item link takes the name the record holds today (the name
it was matched or pushed under).

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tally_link", sa.Column("tally_name", sa.String(500), nullable=True))
    op.execute(
        "UPDATE tally_link SET tally_name = ("
        "SELECT p.legal_name FROM party p WHERE p.id = tally_link.entity_id) "
        "WHERE entity_type = 'party'"
    )
    op.execute(
        "UPDATE tally_link SET tally_name = ("
        "SELECT i.name FROM item i WHERE i.id = tally_link.entity_id) "
        "WHERE entity_type = 'item'"
    )


def downgrade() -> None:
    op.drop_column("tally_link", "tally_name")

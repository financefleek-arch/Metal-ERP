"""Party opening balance — a balance carried from before go-live.

`party.opening_balance` is a manually-entered figure (NOT imported from
Tally): what the party owed you on the day you started billing in this
system. Sign matches the ledger — positive = party owes you, negative = a
customer advance. `opening_balance_as_of` is an optional display date shown
as the statement's first line.

The field is editable in the UI only while the party has zero ledger
history (no finalized invoice, no payment); the router enforces a 409 once
that lock condition holds. Nothing DB-level enforces the lock — it's a
service/router concern, same pattern as the payment invariants in 0017.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("party") as batch:
        batch.add_column(
            sa.Column(
                "opening_balance",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("opening_balance_as_of", sa.Date(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("party") as batch:
        batch.drop_column("opening_balance_as_of")
        batch.drop_column("opening_balance")

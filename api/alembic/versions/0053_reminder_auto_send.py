"""Payment reminders: a firm may let them go out without approval.

  - tenant.reminder_auto_send      False = the shop reviews each day's list (the default)
  - tenant.reminder_auto_allowed   Fleek's per-firm switch; off refuses and stops automatic sending

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("reminder_auto_send", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tenant",
        sa.Column("reminder_auto_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("tenant", "reminder_auto_allowed")
    op.drop_column("tenant", "reminder_auto_send")

"""Tenant default credit period — feeds Collections ageing buckets (F3a).

`tenant.default_credit_days` is the number of days after an invoice's date
that its balance is considered "due". 0 (the default) means "due on the
invoice date", which is exactly the implicit behaviour before this column
existed — so existing tenants see no change to their ageing until they set
a period. A per-party override (`party.credit_days`) is deliberately out of
scope for F3a.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tenant") as batch:
        batch.add_column(
            sa.Column(
                "default_credit_days",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tenant") as batch:
        batch.drop_column("default_credit_days")

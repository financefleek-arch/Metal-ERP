"""Persist the operator's original % discount entry on an invoice line.

`invoice_line.discount` remains the absolute ₹ amount that `domain.tax`
reads for billing math — that never changes. This adds `discount_pct` as a
nullable, purely-cosmetic UI hint: set when the operator typed the line
discount as a percentage (so reopening the draft can show "15%" instead of
a raw ₹ figure that happened to compute from it), null when they typed an
absolute ₹ amount directly. Never read by `domain.tax` or the finalize
transaction.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoice_line",
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoice_line", "discount_pct")

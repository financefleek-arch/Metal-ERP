"""invoice.invoice_discount — an absolute amount off the whole bill, editable
on a draft and applied by domain.tax before round-off.

`discount_total` stays what it is: the frozen sum (line discounts +
invoice discount) written at finalize. `invoice_discount` is the editor
input that feeds it.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoice",
        sa.Column(
            "invoice_discount",
            sa.Numeric(15, 2),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("invoice", "invoice_discount")

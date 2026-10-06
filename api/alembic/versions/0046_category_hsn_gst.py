"""Item categories (our groups): default HSN and GST rate, inherited by new items.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("item_category", sa.Column("hsn_code", sa.String(8), nullable=True))
    op.add_column("item_category", sa.Column("gst_rate", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("item_category", "gst_rate")
    op.drop_column("item_category", "hsn_code")

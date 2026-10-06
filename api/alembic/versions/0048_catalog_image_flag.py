"""Price-list rows: a flag on a photo that probably needs a look.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("supplier_catalog_item", sa.Column("image_flag", sa.String(12), nullable=True))


def downgrade() -> None:
    op.drop_column("supplier_catalog_item", "image_flag")

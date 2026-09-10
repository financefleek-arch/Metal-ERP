"""tally-agent: per-shop cloud-retention override for backup uploads.

One nullable column on `backup_shop`:
- `backup_retention_count` — keep at most this many confirmed backups for
  the shop, pruned oldest-first after each new confirm. NULL = fall back to
  `settings.tally_backup_retention_count` (global default).

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backup_shop", sa.Column("backup_retention_count", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("backup_shop", "backup_retention_count")

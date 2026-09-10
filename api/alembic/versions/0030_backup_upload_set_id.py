"""tally-agent: group a backup run's files into a set for retention.

One TallyPrime backup run is several files — a `TBK…900` manifest plus one
or more `TDBK…001/.002` data parts. The agent now tags every file of a run
with a shared `set_id` so cloud retention prunes whole sets, never a
half-set that can't be restored.

One nullable column on `backup_upload`:
- `set_id` — shared per backup run. NULL for pre-0030 rows and for
  standalone uploads (e.g. the masters-XML pull); retention treats a NULL
  as a singleton set keyed by the row id.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backup_upload", sa.Column("set_id", sa.String(40), nullable=True))
    op.create_index("ix_backup_upload_set_id", "backup_upload", ["set_id"])


def downgrade() -> None:
    op.drop_index("ix_backup_upload_set_id", table_name="backup_upload")
    op.drop_column("backup_upload", "set_id")

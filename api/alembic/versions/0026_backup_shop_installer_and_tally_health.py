"""tally-agent: cache the per-shop installer + record the Tally reachability signal.

Three nullable columns on `backup_shop`:
- `installer_r2_key`  — the cached installer zip's R2 key, set when the shop's
  API key is minted or rotated. NULL until the first successful build.
- `last_tally_ok_at`  — the last checkin where the agent reported it could
  reach TallyPrime's HTTP gateway.
- `last_tally_status` — 'connected' | 'refused' | 'no_company' | 'unknown';
  NULL means the agent has never reported either way.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backup_shop", sa.Column("installer_r2_key", sa.String(500), nullable=True))
    op.add_column(
        "backup_shop", sa.Column("last_tally_ok_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("backup_shop", sa.Column("last_tally_status", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("backup_shop", "last_tally_status")
    op.drop_column("backup_shop", "last_tally_ok_at")
    op.drop_column("backup_shop", "installer_r2_key")

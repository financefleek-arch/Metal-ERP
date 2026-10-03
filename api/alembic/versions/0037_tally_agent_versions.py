"""Tally agent: version tracking + auto-update bookkeeping on backup_shop.

  - agent_version / agent_version_at   what the agent last reported at checkin
  - os_version                         e.g. 'Microsoft Windows NT 10.0.22631.0 / X64'
  - target_agent_version               ops pin (canary / rollback); NULL = follow `latest`
  - installer_agent_version            agent version baked into the cached installer zip
  - last_update_status                 'failed:<ver>' after a rolled-back update; NULL otherwise

All nullable, no backfill: an agent that predates auto-update simply never
reports a version and is never offered an update.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_shop") as batch:
        batch.add_column(sa.Column("agent_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("agent_version_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("os_version", sa.String(120), nullable=True))
        batch.add_column(sa.Column("target_agent_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("installer_agent_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("last_update_status", sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backup_shop") as batch:
        batch.drop_column("last_update_status")
        batch.drop_column("installer_agent_version")
        batch.drop_column("target_agent_version")
        batch.drop_column("os_version")
        batch.drop_column("agent_version_at")
        batch.drop_column("agent_version")

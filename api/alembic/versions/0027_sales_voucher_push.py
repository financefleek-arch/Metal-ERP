"""F1b-1: sales-voucher-out — entity reference on tally_sync_job.

Two nullable columns on `tally_sync_job` so a `push_sales` job can be
looked up by the ERP record it's pushing (`entity_type='invoice'`,
`entity_id=invoice.id`), without scanning `counts`/`error` JSON. Null for
F1a's `pull_masters` jobs, which reference no single record.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tally_sync_job", sa.Column("entity_type", sa.String(10), nullable=True))
    op.add_column("tally_sync_job", sa.Column("entity_id", sa.String(36), nullable=True))
    op.create_index(
        "ix_tally_sync_job_entity", "tally_sync_job", ["entity_type", "entity_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tally_sync_job_entity", table_name="tally_sync_job")
    op.drop_column("tally_sync_job", "entity_id")
    op.drop_column("tally_sync_job", "entity_type")

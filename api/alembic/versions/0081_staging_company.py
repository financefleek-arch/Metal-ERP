"""Staged Tally batches belong to a company (S1.6).

`staging_tally_party.company_id` and `staging_tally_item.company_id` name the company a pull ran for;
null stays for a file upload. The one-uncommitted-batch rule is now per company. Backfill: rows from a
pull take their job's company.

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("staging_tally_party", "staging_tally_item")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("company_id", sa.String(36), nullable=True))
        op.create_foreign_key(f"fk_{table}_company", table, "tally_company", ["company_id"], ["id"])
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        op.execute(
            f"UPDATE {table} SET company_id = ("
            f"SELECT j.company_id FROM tally_sync_job j WHERE j.id = {table}.sync_job_id) "
            f"WHERE sync_job_id IS NOT NULL"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_constraint(f"fk_{table}_company", table, type_="foreignkey")
        op.drop_column(table, "company_id")

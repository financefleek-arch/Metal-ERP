"""Indexes for the item / party list + search at 10k rows.

The list endpoints scan `WHERE tenant_id = ? AND <status> ORDER BY lower(name)`
and the type-ahead fires an OR of `LIKE '%q%'` / trigram-similarity branches.
At 2-3k rows a seq scan is fine; by 10k it isn't. This adds:

  * composite btree on the browse sort key so the default list and keyset
    paging are index-ordered range scans, not sort-the-world
  * GIN trigram on `lower(item.name)` / `lower(party.legal_name)` so the
    `LIKE '%q%'` and `%` / `%>` similarity branches of the search OR each
    become bitmap index scans

`CREATE INDEX CONCURRENTLY` can't run inside Alembic's transaction, so this
migration opens an autocommit block. That means a partial failure leaves a
valid-so-far set of indexes; re-running `alembic upgrade head` is safe
(every statement is `IF NOT EXISTS`).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# name -> DDL. CONCURRENTLY + IF NOT EXISTS so a rerun is a no-op.
_INDEXES: dict[str, str] = {
    "ix_item_tenant_browse": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_item_tenant_browse "
        "ON item (tenant_id, status, lower(name), id) "
        "WHERE merged_into_id IS NULL"
    ),
    "ix_item_name_lower_trgm": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_item_name_lower_trgm "
        "ON item USING gin (lower(name) gin_trgm_ops)"
    ),
    "ix_party_tenant_browse": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_party_tenant_browse "
        "ON party (tenant_id, status, lower(legal_name), id)"
    ),
    "ix_party_legal_name_lower_trgm": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_party_legal_name_lower_trgm "
        "ON party USING gin (lower(legal_name) gin_trgm_ops)"
    ),
}


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return  # SQLite (tests) — the query planner doesn't need these
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for ddl in _INDEXES.values():
            op.execute(ddl)


def downgrade() -> None:
    if not _is_postgres():
        return
    with op.get_context().autocommit_block():
        for name in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

"""fuzzy trigram index on item.name_normalized

Separated from 0001 so the base schema has no extension dependency. The
metalerp database is created with `CREATE EXTENSION pg_trgm` (infra repo:
postgres/init/01-create-databases.sql + the one-time manual step), so
this is a no-op CREATE EXTENSION IF NOT EXISTS plus the GIN index the
type-ahead search uses.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-31
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_item_name_normalized_trgm "
        "ON item USING gin (name_normalized gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_item_alias_normalized_trgm "
        "ON item_alias USING gin (alias_normalized gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_item_alias_normalized_trgm")
    op.execute("DROP INDEX IF EXISTS ix_item_name_normalized_trgm")
    # leave the extension in place — other things may rely on it

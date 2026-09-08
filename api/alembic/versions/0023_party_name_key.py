"""party.legal_name_normalized — the de-dup key behind resolve_party

Mirrors item.name_normalized. Two-phase because NOT NULL needs a value for
existing rows and CREATE INDEX CONCURRENTLY can't run inside Alembic's
transaction (same autocommit-block pattern as 0015):

  1. add the column nullable
  2. crude in-DB backfill (lower + strip non-alnum + collapse) so NOT NULL
     can be set now. This is a LOWER-QUALITY key than normalize_name() —
     no tenant synonyms, no NFKD accent-fold. tools/backfill_party_namekey.py
     replaces it with the real pipeline output right after deploy.
  3. NOT NULL
  4. composite btree (tenant_id, legal_name_normalized) for the exact-key rung
  5. trgm GIN on legal_name_normalized for the fuzzy rung (Postgres only)

No UNIQUE constraint: prod has existing duplicates and the router (structured
409 + candidate picker), not a raw IntegrityError, owns the decision.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. nullable column
    op.add_column(
        "party", sa.Column("legal_name_normalized", sa.String(length=200), nullable=True)
    )

    # 2. crude backfill — good enough to satisfy NOT NULL; the real key comes
    #    from tools/backfill_party_namekey.py post-deploy.
    if is_pg:
        op.execute(
            r"""
            UPDATE party SET legal_name_normalized =
              btrim(regexp_replace(lower(legal_name), '[^a-z0-9]+', ' ', 'g'))
            """
        )
    else:  # sqlite (tests) — collapse handled well enough by trim/lower
        op.execute(
            "UPDATE party SET legal_name_normalized = lower(trim(legal_name)) "
            "WHERE legal_name_normalized IS NULL"
        )
    op.execute(
        "UPDATE party SET legal_name_normalized = '' "
        "WHERE legal_name_normalized IS NULL"
    )

    # 3. NOT NULL (+ keep a server default so a bare INSERT that omits it still
    #    works; the router always sets the real value)
    op.alter_column(
        "party",
        "legal_name_normalized",
        existing_type=sa.String(length=200),
        nullable=False,
        server_default="",
    )

    # 4. composite btree for the exact-key lookup
    op.create_index(
        "ix_party_tenant_namekey",
        "party",
        ["tenant_id", "legal_name_normalized"],
    )

    # 5. trgm GIN for the fuzzy rung — CONCURRENTLY, autocommit block, PG only
    if is_pg:
        with op.get_context().autocommit_block():
            op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_party_namekey_trgm "
                "ON party USING gin (legal_name_normalized gin_trgm_ops)"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_party_namekey_trgm")
    op.drop_index("ix_party_tenant_namekey", table_name="party")
    op.drop_column("party", "legal_name_normalized")

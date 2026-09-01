"""party status + provenance + last-seen; tenant dormancy window

Adds to `party`:
  - status        active | archived  (archived drops out of default lists/pickers)
  - source        manual | inward_bill | tally_import  (provenance, display-only)
  - source_ref    the inward_bill.id when source=inward_bill
  - last_txn_at   forward-only stamp from invoice-finalize / inward-approve
And to `tenant`:
  - dormant_party_days  INT default 180

Plus a pg_trgm GIN index on party.legal_name for fuzzy name search
(Postgres only; the extension is created in 0002).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "party",
        sa.Column("status", sa.String(10), server_default="active", nullable=False),
    )
    op.add_column(
        "party",
        sa.Column("source", sa.String(20), server_default="manual", nullable=False),
    )
    op.add_column("party", sa.Column("source_ref", sa.String(36), nullable=True))
    op.add_column(
        "party",
        sa.Column("last_txn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_party_status", "party", ["status"])

    op.add_column(
        "tenant",
        sa.Column(
            "dormant_party_days", sa.Integer(), server_default="180", nullable=False
        ),
    )

    # Fuzzy name search — Postgres only. pg_trgm is created in 0002.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_party_legal_name_trgm "
            "ON party USING gin (legal_name gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_party_legal_name_trgm")

    op.drop_column("tenant", "dormant_party_days")
    op.drop_index("ix_party_status", table_name="party")
    op.drop_column("party", "last_txn_at")
    op.drop_column("party", "source_ref")
    op.drop_column("party", "source")
    op.drop_column("party", "status")

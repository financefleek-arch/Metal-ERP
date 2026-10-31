"""An item's Tally push state is kept per Tally company (S1.2b).

`item_tally_state` holds, per (company, item): push status, the price last sent, the price Tally showed
at the last check, and when it was sent. Backfill: every item that carries state moves to its firm's
default company (a firm had one company until 0075). The old `item.tally_*` columns stay, unread.

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_tally_state",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("tally_company.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id"), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="none"),
        sa.Column("price", sa.Numeric(15, 2), nullable=True),
        sa.Column("seen_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("company_id", "item_id", name="uq_item_tally_state"),
    )
    op.create_index("ix_item_tally_state_tenant_id", "item_tally_state", ["tenant_id"])
    op.create_index("ix_item_tally_state_company_id", "item_tally_state", ["company_id"])
    op.create_index("ix_item_tally_state_item_id", "item_tally_state", ["item_id"])

    if op.get_bind().dialect.name != "postgresql":
        return  # the backfill uses Postgres syntax; a fresh SQLite database has no data to move
    op.execute(
        """
        INSERT INTO item_tally_state
            (id, tenant_id, company_id, item_id, status, price, seen_price, pushed_at)
        SELECT gen_random_uuid()::text, i.tenant_id, c.id, i.id, i.tally_status,
               i.tally_price, i.tally_seen_price, i.tally_pushed_at
        FROM item i
        JOIN tally_company c ON c.tenant_id = i.tenant_id AND c.is_default
        WHERE i.tally_status <> 'none' OR i.tally_price IS NOT NULL
              OR i.tally_seen_price IS NOT NULL OR i.tally_pushed_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("item_tally_state")

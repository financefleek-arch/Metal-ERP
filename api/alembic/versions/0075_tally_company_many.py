"""A firm can link more than one Tally company (S1.1).

Drops "one Tally company per firm" and adds what tells companies apart: kind, default flag, series
prefix, document label, reminders switch, visible roles, status. Exactly one default per firm is a
partial unique index. Every existing company becomes its firm's default, so nothing changes for a
firm with one company: kind is `gst` where the firm has GST on, else `other`.

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_tally_company_tenant", "tally_company", type_="unique")

    op.add_column(
        "tally_company",
        sa.Column("kind", sa.String(12), nullable=False, server_default="other"),
    )
    # server_default true backfills the existing rows as defaults; new rows say it themselves.
    op.add_column(
        "tally_company",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("tally_company", "is_default", server_default=sa.false())
    op.add_column("tally_company", sa.Column("series_prefix", sa.String(20), nullable=True))
    op.add_column("tally_company", sa.Column("document_label", sa.String(40), nullable=True))
    op.add_column(
        "tally_company",
        sa.Column("include_in_reminders", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "tally_company",
        sa.Column(
            "visible_roles",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "tally_company",
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
    )

    op.execute(
        "UPDATE tally_company SET kind = 'gst' "
        "WHERE tenant_id IN (SELECT id FROM tenant WHERE gst_enabled)"
    )
    op.create_index(
        "uq_tally_company_default",
        "tally_company",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
        sqlite_where=sa.text("is_default"),
    )


def downgrade() -> None:
    # Only safe while every firm still has a single company.
    op.drop_index("uq_tally_company_default", table_name="tally_company")
    for col in (
        "status",
        "visible_roles",
        "include_in_reminders",
        "document_label",
        "series_prefix",
        "is_default",
        "kind",
    ):
        op.drop_column("tally_company", col)
    op.create_unique_constraint("uq_tally_company_tenant", "tally_company", ["tenant_id"])

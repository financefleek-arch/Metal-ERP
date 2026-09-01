"""staging_tally_party — holds a parsed Tally masters-XML party import
between upload and commit.

No changes to `party`: 0003 already added source / source_ref, and
tally_guid has existed since 0001.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

_JSON = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staging_tally_party",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("tally_guid", sa.String(64)),
        sa.Column("ledger_name", sa.String(500), nullable=False),
        sa.Column("parent_group", sa.String(200)),
        sa.Column("gstin", sa.String(20)),
        sa.Column("pan", sa.String(20)),
        sa.Column("state_name", sa.String(100)),
        sa.Column("phone", sa.String(60)),
        sa.Column("email", sa.String(200)),
        sa.Column("address_lines_json", _JSON),
        sa.Column("pincode", sa.String(20)),
        sa.Column("raw_xml", sa.Text()),
        sa.Column("proposed_role", sa.String(10), server_default="customer", nullable=False),
        sa.Column("match_method", sa.String(20), server_default="none", nullable=False),
        sa.Column("match_party_id", sa.String(36), sa.ForeignKey("party.id")),
        sa.Column("flags_json", _JSON),
        sa.Column("decision", sa.String(10), server_default="pending", nullable=False),
        sa.Column("role_override", sa.String(10)),
        sa.Column("link_party_id", sa.String(36), sa.ForeignKey("party.id")),
        sa.Column("edited_name", sa.String(200)),
        sa.Column("committed_as", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_staging_tally_party_tenant_id", "staging_tally_party", ["tenant_id"]
    )
    op.create_index(
        "ix_staging_tally_party_batch_id", "staging_tally_party", ["batch_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_staging_tally_party_batch_id", table_name="staging_tally_party")
    op.drop_index("ix_staging_tally_party_tenant_id", table_name="staging_tally_party")
    op.drop_table("staging_tally_party")

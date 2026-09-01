"""staging_tally_item — holds a parsed Tally stock-items XML import
between upload and commit.

No changes to `item`: 0006/0007 already have every column the importer
writes (sku, category_id, rate_mode, metal/shape/grade/size_text).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

_JSON = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staging_tally_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("tally_guid", sa.String(64)),
        sa.Column("stock_name", sa.String(500), nullable=False),
        sa.Column("parent_group", sa.String(200)),
        sa.Column("base_units", sa.String(40)),
        sa.Column("hsn", sa.String(20)),
        sa.Column("gst_rate", sa.Numeric(5, 2)),
        sa.Column("standard_rate", sa.Numeric(15, 2)),
        sa.Column("raw_xml", sa.Text()),
        sa.Column("proposed_type", sa.String(10), server_default="bulk", nullable=False),
        sa.Column("proposed_uom", sa.String(20)),
        sa.Column("proposed_rate_mode", sa.String(10), server_default="piece", nullable=False),
        sa.Column("parsed_metal", sa.String(20)),
        sa.Column("parsed_shape", sa.String(24)),
        sa.Column("parsed_grade", sa.String(32)),
        sa.Column("parsed_size_text", sa.String(60)),
        sa.Column("parsed_sku", sa.String(64)),
        sa.Column("match_method", sa.String(10), server_default="none", nullable=False),
        sa.Column("match_item_id", sa.String(36), sa.ForeignKey("item.id")),
        sa.Column("guid_fillable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("flags_json", _JSON),
        sa.Column("decision", sa.String(10), server_default="pending", nullable=False),
        sa.Column("type_override", sa.String(10)),
        sa.Column("edited_name", sa.String(200)),
        sa.Column("seed_hsn", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("committed_as", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_staging_tally_item_tenant_id", "staging_tally_item", ["tenant_id"])
    op.create_index("ix_staging_tally_item_batch_id", "staging_tally_item", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_staging_tally_item_batch_id", table_name="staging_tally_item")
    op.drop_index("ix_staging_tally_item_tenant_id", table_name="staging_tally_item")
    op.drop_table("staging_tally_item")

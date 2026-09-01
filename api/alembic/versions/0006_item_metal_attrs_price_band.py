"""item: metal-trade attributes, units & conversion, price band; + reserved
dormant columns for the price engine and Stage 2/3.

`last_purchase_rate` / `last_purchased_at` were already added by 0004
(inward module) — not touched here. `item.status` already defaults to
'unconfirmed'. pg_trgm indexes on name_normalized / alias_normalized
already exist (0002).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_N = sa.Numeric


def upgrade() -> None:
    # metal-trade attributes
    op.add_column("item", sa.Column("metal", sa.String(20)))
    op.add_column("item", sa.Column("shape", sa.String(24)))
    op.add_column("item", sa.Column("grade", sa.String(32)))
    op.add_column("item", sa.Column("size_text", sa.String(60)))
    op.add_column("item", sa.Column("thickness_mm", _N(9, 2)))
    op.add_column("item", sa.Column("width_mm", _N(9, 2)))
    op.add_column("item", sa.Column("length_mm", _N(9, 2)))
    op.add_column("item", sa.Column("finish", sa.String(24)))

    # units & conversion
    op.add_column("item", sa.Column("secondary_uom", sa.String(20)))
    op.add_column("item", sa.Column("conversion_factor", _N(12, 4)))
    op.add_column("item", sa.Column("weight_per_uom", _N(12, 3)))
    op.add_column("item", sa.Column("purchase_uom", sa.String(20)))

    # price band
    op.add_column("item", sa.Column("price_min", _N(15, 2)))
    op.add_column("item", sa.Column("price_max", _N(15, 2)))

    # reserved — price engine (dormant)
    op.add_column("item", sa.Column("markup_pct", _N(5, 2)))
    op.add_column("item", sa.Column("suggested_rate", _N(15, 2)))
    op.add_column("item", sa.Column("suggested_rate_basis", sa.String(120)))
    op.add_column("item", sa.Column("suggested_rate_at", sa.DateTime(timezone=True)))
    op.add_column(
        "item",
        sa.Column(
            "price_review_pending", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )

    # reserved — Stage 2/3 (dormant)
    op.add_column("item", sa.Column("barcode", sa.String(64)))
    op.add_column("item", sa.Column("sku", sa.String(64)))
    op.add_column("item", sa.Column("reorder_level", _N(15, 3)))
    op.create_index("ix_item_barcode", "item", ["barcode"])

    op.add_column("item", sa.Column("notes", sa.String(1000)))

    op.add_column("tenant", sa.Column("default_markup_pct", _N(5, 2)))


def downgrade() -> None:
    op.drop_column("tenant", "default_markup_pct")
    op.drop_column("item", "notes")
    op.drop_index("ix_item_barcode", table_name="item")
    for col in (
        "reorder_level",
        "sku",
        "barcode",
        "price_review_pending",
        "suggested_rate_at",
        "suggested_rate_basis",
        "suggested_rate",
        "markup_pct",
        "price_max",
        "price_min",
        "purchase_uom",
        "weight_per_uom",
        "conversion_factor",
        "secondary_uom",
        "finish",
        "length_mm",
        "width_mm",
        "thickness_mm",
        "size_text",
        "grade",
        "shape",
        "metal",
    ):
        op.drop_column("item", col)

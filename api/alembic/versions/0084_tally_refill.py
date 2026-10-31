"""Refill of parties and items from Tally: stock on hand, a party snapshot, last price per party and item,
and the markers that make the next read incremental.

All additive: new columns on `item_tally_state` and `tally_company`, and two new tables. Nothing existing is changed and
nothing is backfilled, so the migration cannot lose data. Chains off 0083, so 0076-0083 must be committed
and deployed with it.

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("item_tally_state", sa.Column("stock_qty", sa.Numeric(15, 3), nullable=True))
    op.add_column("item_tally_state", sa.Column("stock_unit", sa.String(20), nullable=True))
    op.add_column("item_tally_state", sa.Column("stock_rate", sa.Numeric(15, 2), nullable=True))
    op.add_column("item_tally_state", sa.Column("stock_value", sa.Numeric(15, 2), nullable=True))
    op.add_column(
        "item_tally_state", sa.Column("stock_as_of", sa.DateTime(timezone=True), nullable=True)
    )

    op.add_column("tally_company", sa.Column("ledgers_alter_id", sa.Integer(), nullable=True))
    op.add_column("tally_company", sa.Column("items_alter_id", sa.Integer(), nullable=True))
    op.add_column(
        "tally_company", sa.Column("full_refill_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "tally_company",
        sa.Column("auto_refresh", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "party_tally_snapshot",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("tally_company.id"), nullable=False),
        sa.Column("party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=False),
        sa.Column("outstanding", sa.Numeric(15, 2), nullable=True),
        sa.Column("outstanding_as_of", sa.Date(), nullable=True),
        sa.Column("oldest_bill_date", sa.Date(), nullable=True),
        sa.Column("open_bills", sa.Integer(), nullable=True),
        sa.Column("last_sale_date", sa.Date(), nullable=True),
        sa.Column("last_sale_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("sales_in_window", sa.Integer(), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kept_ours_outstanding", sa.Numeric(15, 2), nullable=True),
        sa.UniqueConstraint("company_id", "party_id", name="uq_party_tally_snapshot"),
    )
    op.create_index("ix_party_tally_snapshot_tenant_id", "party_tally_snapshot", ["tenant_id"])
    op.create_index("ix_party_tally_snapshot_company_id", "party_tally_snapshot", ["company_id"])
    op.create_index("ix_party_tally_snapshot_party_id", "party_tally_snapshot", ["party_id"])

    op.create_table(
        "party_item_price",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("tally_company.id"), nullable=False),
        sa.Column("party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id"), nullable=False),
        sa.Column("rate", sa.Numeric(15, 2), nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("quantity", sa.Numeric(15, 3), nullable=True),
        sa.Column("sold_on", sa.Date(), nullable=False),
        sa.Column("voucher_no", sa.String(40), nullable=True),
        sa.UniqueConstraint("company_id", "party_id", "item_id", name="uq_party_item_price"),
    )
    op.create_index("ix_party_item_price_tenant_id", "party_item_price", ["tenant_id"])
    op.create_index("ix_party_item_price_company_id", "party_item_price", ["company_id"])
    op.create_index("ix_party_item_price_party_id", "party_item_price", ["party_id"])
    op.create_index("ix_party_item_price_item_id", "party_item_price", ["item_id"])


def downgrade() -> None:
    for col in ("auto_refresh", "full_refill_at", "items_alter_id", "ledgers_alter_id"):
        op.drop_column("tally_company", col)
    op.drop_table("party_item_price")
    op.drop_table("party_tally_snapshot")
    for col in ("stock_as_of", "stock_value", "stock_rate", "stock_unit", "stock_qty"):
        op.drop_column("item_tally_state", col)

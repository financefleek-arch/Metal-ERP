"""Customer ordering O2: orders placed from a share link.

  - customer_order        one order (number, status, customer details, totals, status token)
  - customer_order_line   its lines (name, code, whole packs, rate per pack, amount)

Order numbers (ORD-0042) come from a counter per shop in `code_sequence` (prefix ORD).

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_order",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("link_id", sa.String(36), sa.ForeignKey("catalog_share_link.id"), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="new"),
        sa.Column("status_token", sa.String(40), nullable=False),
        sa.Column("customer_name", sa.String(100), nullable=False),
        sa.Column("customer_phone", sa.String(20), nullable=False),
        sa.Column("customer_firm", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("wa_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("matched_party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("total", sa.Numeric(15, 2), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoice.id"), nullable=True),
        sa.Column("dedupe_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "number", name="uq_customer_order_tenant_number"),
        sa.UniqueConstraint("status_token", name="uq_customer_order_status_token"),
    )
    op.create_index("ix_customer_order_tenant_id", "customer_order", ["tenant_id"])
    op.create_index("ix_customer_order_tenant_status", "customer_order", ["tenant_id", "status"])
    op.create_index("ix_customer_order_dedupe", "customer_order", ["tenant_id", "dedupe_hash"])
    op.create_table(
        "customer_order_line",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("customer_order.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id"), nullable=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("pack_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rate", sa.Numeric(15, 2), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_order_line_order_id", "customer_order_line", ["order_id"])
    op.create_index("ix_customer_order_line_item_id", "customer_order_line", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_order_line_item_id", table_name="customer_order_line")
    op.drop_index("ix_customer_order_line_order_id", table_name="customer_order_line")
    op.drop_table("customer_order_line")
    op.drop_index("ix_customer_order_dedupe", table_name="customer_order")
    op.drop_index("ix_customer_order_tenant_status", table_name="customer_order")
    op.drop_index("ix_customer_order_tenant_id", table_name="customer_order")
    op.drop_table("customer_order")

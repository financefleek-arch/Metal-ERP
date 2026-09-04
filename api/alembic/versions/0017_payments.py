"""Payments: party-ledger payment tracking, Tally bill-wise-allocation style.

A payment records money received from a party and is split across one or
more `payment_allocation` rows — either `against_invoice` (ties the amount
to one open invoice's balance) or `on_account` (an unapplied credit sitting
against the party only, no invoice). A payment is created posted, no
draft/finalize split; reversal (cheque bounce / wrong entry) is a pure
`status` flip to "reversed" + timestamp — allocation rows are never
deleted, so balance queries filter `payment.status == 'posted'` and a
reversed payment stops counting automatically while the audit trail stays
intact.

`voucher_no` is a gap-free per-tenant sequence, claimed the same way
`invoice.number` is (SELECT ... FOR UPDATE on `number_sequence`, using a
dedicated series so it doesn't collide with the invoice numbering line).

The invoice_id-null-iff-type=on_account invariant is intentionally NOT a
DB-level multi-column CHECK constraint (SQLite vs Postgres portability for
this project) — it's enforced in the service/router layer instead, right
alongside the tenant/party ownership checks that already have to live
there. `amount > 0` IS enforced at the DB level since it's a single-column
check both dialects handle identically.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("party_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("ref_no", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("voucher_no", sa.Integer(), nullable=True),
        sa.Column("ledger_name", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="posted"),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.ForeignKeyConstraint(["party_id"], ["party.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "voucher_no", name="uq_payment_tenant_voucher_no"),
    )
    op.create_index("ix_payment_tenant_id", "payment", ["tenant_id"])
    op.create_index("ix_payment_party_id", "payment", ["party_id"])
    op.create_index("ix_payment_status", "payment", ["status"])

    op.create_table(
        "payment_allocation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payment.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoice.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
    )
    op.create_index("ix_payment_allocation_payment_id", "payment_allocation", ["payment_id"])
    op.create_index("ix_payment_allocation_invoice_id", "payment_allocation", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_allocation_invoice_id", table_name="payment_allocation")
    op.drop_index("ix_payment_allocation_payment_id", table_name="payment_allocation")
    op.drop_table("payment_allocation")
    op.drop_index("ix_payment_status", table_name="payment")
    op.drop_index("ix_payment_party_id", table_name="payment")
    op.drop_index("ix_payment_tenant_id", table_name="payment")
    op.drop_table("payment")

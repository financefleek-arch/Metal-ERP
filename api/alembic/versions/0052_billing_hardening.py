"""Billing hardening: who prepared a draft, and payment reminders.

  - invoice.created_by_user_id / last_edited_by_user_id   who prepared and last touched a draft
  - tenant.reminder_enabled / reminder_days               per-firm reminder policy (off by default)
  - payment_reminder                                      one proposal per party per day, sent by the shop

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("created_by_user_id", sa.String(36), nullable=True))
    op.add_column("invoice", sa.Column("last_edited_by_user_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_invoice_created_by_user", "invoice", "app_user", ["created_by_user_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_invoice_last_edited_by_user", "invoice", "app_user", ["last_edited_by_user_id"], ["id"]
    )

    op.add_column(
        "tenant",
        sa.Column("reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tenant",
        sa.Column("reminder_days", sa.String(40), nullable=False, server_default="7,15,30"),
    )

    op.create_table(
        "payment_reminder",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),  # invoice | statement
        sa.Column(
            "invoice_id",
            sa.String(36),
            sa.ForeignKey("invoice.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("invoice_ids", sa.JSON(), nullable=True),  # every invoice this one covers
        sa.Column("stage_days", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("oldest_overdue_days", sa.Integer(), nullable=False),
        sa.Column("proposed_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="proposed"),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("decided_by_user_id", sa.String(36), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("whatsapp_message_id", sa.String(36), nullable=True),
        sa.Column("dedupe_key", sa.String(80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_payment_reminder_dedupe"),
    )
    op.create_index(
        "ix_payment_reminder_tenant_status", "payment_reminder", ["tenant_id", "status"]
    )
    op.create_index("ix_payment_reminder_party", "payment_reminder", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_reminder_party", table_name="payment_reminder")
    op.drop_index("ix_payment_reminder_tenant_status", table_name="payment_reminder")
    op.drop_table("payment_reminder")
    op.drop_column("tenant", "reminder_days")
    op.drop_column("tenant", "reminder_enabled")
    op.drop_constraint("fk_invoice_last_edited_by_user", "invoice", type_="foreignkey")
    op.drop_constraint("fk_invoice_created_by_user", "invoice", type_="foreignkey")
    op.drop_column("invoice", "last_edited_by_user_id")
    op.drop_column("invoice", "created_by_user_id")

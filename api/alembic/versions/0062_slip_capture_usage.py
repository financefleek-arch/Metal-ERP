"""Slip-capture usage counter: one row per photo sent for reading.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slip_capture",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("model", sa.String(40), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("line_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("image_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_slip_capture_tenant_created", "slip_capture", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_slip_capture_tenant_created", table_name="slip_capture")
    op.drop_table("slip_capture")

"""Cache of a firm's licence as reported by the Fleek control plane.

  - tenant_entitlement   one row per linked firm: last good answer, when it may be relied on, last refresh
  error

See docs/PLAN-fleek-control-plane.md (P2c).

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_entitlement",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenant_entitlement_tenant_id", "tenant_entitlement", ["tenant_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenant_entitlement_tenant_id", table_name="tenant_entitlement")
    op.drop_table("tenant_entitlement")

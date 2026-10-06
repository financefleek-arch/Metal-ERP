"""Customer ordering O1: catalog share links, and the shop-wide order settings.

  - catalog_share_link      a public, revocable link to a catalog (token, selection, expiry, views)
  - tenant.order_min_value  smallest order value accepted (one setting for the shop)
  - tenant.order_terms_line one terms line shown on the customer catalog page

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_share_link",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("token", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "selection_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("include_expected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token", name="uq_catalog_share_link_token"),
    )
    op.create_index("ix_catalog_share_link_tenant_id", "catalog_share_link", ["tenant_id"])
    op.add_column("tenant", sa.Column("order_min_value", sa.Numeric(15, 2), nullable=True))
    op.add_column("tenant", sa.Column("order_terms_line", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant", "order_terms_line")
    op.drop_column("tenant", "order_min_value")
    op.drop_index("ix_catalog_share_link_tenant_id", table_name="catalog_share_link")
    op.drop_table("catalog_share_link")

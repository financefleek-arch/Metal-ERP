"""Item photos: a media table and two item columns.

  - media_asset    one row per stored image (normalised main + thumbnail, content-hashed,
                   unique per firm); many things may point at one row
  - item.primary_media_id   the item's photo
  - item.is_stock           false for non-goods lines; unused until stock is tracked

Nothing to back-fill: items start without a photo (promoting a catalog row copies its photo).

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_asset",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("thumb_key", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False, server_default="image/webp"),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("thumb_bytes", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(12), nullable=False, server_default="upload"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_media_tenant_sha"),
    )
    op.create_index("ix_media_asset_tenant_id", "media_asset", ["tenant_id"])
    op.add_column(
        "item", sa.Column("primary_media_id", sa.String(36), sa.ForeignKey("media_asset.id"))
    )
    op.create_index("ix_item_primary_media_id", "item", ["primary_media_id"])
    op.add_column(
        "item",
        sa.Column("is_stock", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("item", "is_stock")
    op.drop_index("ix_item_primary_media_id", table_name="item")
    op.drop_column("item", "primary_media_id")
    op.drop_index("ix_media_asset_tenant_id", table_name="media_asset")
    op.drop_table("media_asset")

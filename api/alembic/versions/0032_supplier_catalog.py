"""Supplier Catalog (S0): tenant flag + settings, catalog tables, code sequence.

Adds the Supplier Catalog module (docs/EXECUTION-PLAN-supplier-catalog.md):
  - tenant.ext_supplier_catalog        BOOLEAN DEFAULT false  (feature flag)
  - tenant.catalog_code_prefix         VARCHAR(8) NULL        (default item-code prefix)
  - tenant.catalog_group_create_policy VARCHAR(12) DEFAULT 'auto'
  - supplier_catalog, supplier_catalog_item  (tenant-scoped)
  - code_sequence  (next item-code number per tenant + prefix)

No extension dependency; plain create_table / add_column.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(15, 2)
_MULT = sa.Numeric(6, 3)


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("ext_supplier_catalog", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("tenant", sa.Column("catalog_code_prefix", sa.String(8), nullable=True))
    op.add_column(
        "tenant",
        sa.Column(
            "catalog_group_create_policy",
            sa.String(12),
            nullable=False,
            server_default="auto",
        ),
    )

    op.create_table(
        "supplier_catalog",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("supplier_party_id", sa.String(36), sa.ForeignKey("party.id")),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(300)),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("code_prefix", sa.String(8), nullable=False),
        sa.Column("multiplier", _MULT, nullable=False, server_default="1.000"),
        sa.Column("rounding_step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="extracting"),
        sa.Column("error_message", sa.Text()),
        sa.Column("supersedes_catalog_id", sa.String(36), sa.ForeignKey("supplier_catalog.id")),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "source_sha256", name="uq_supplier_catalog_tenant_sha"),
    )
    op.create_index("ix_supplier_catalog_tenant_id", "supplier_catalog", ["tenant_id"])
    op.create_index(
        "ix_supplier_catalog_supplier_party_id", "supplier_catalog", ["supplier_party_id"]
    )

    op.create_table(
        "supplier_catalog_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "catalog_id", sa.String(36), sa.ForeignKey("supplier_catalog.id"), nullable=False
        ),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("supplier_code", sa.String(60), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name_raw", sa.String(400), nullable=False),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("brand", sa.String(40)),
        sa.Column("size_text", sa.String(60)),
        sa.Column("pack_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("carton_qty", sa.Integer()),
        sa.Column("cost_price", _MONEY, nullable=False),
        sa.Column("multiplier_override", _MULT),
        sa.Column("sell_price", _MONEY, nullable=False),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("item_category.id")),
        sa.Column("suggested_group", sa.String(60)),
        sa.Column("image_key", sa.String(300)),
        sa.Column("image_w", sa.Integer()),
        sa.Column("image_h", sa.Integer()),
        sa.Column("image_sha256", sa.String(64)),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id")),
        sa.Column("tally_status", sa.String(8), nullable=False, server_default="none"),
        sa.Column("tally_pushed_at", sa.DateTime(timezone=True)),
        sa.Column("price_change", sa.String(8)),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_catalog_item_tenant_code"),
        sa.UniqueConstraint("catalog_id", "supplier_code", name="uq_catalog_item_supplier_code"),
    )
    op.create_index("ix_supplier_catalog_item_tenant_id", "supplier_catalog_item", ["tenant_id"])
    op.create_index(
        "ix_supplier_catalog_item_catalog_id", "supplier_catalog_item", ["catalog_id"]
    )
    op.create_index("ix_supplier_catalog_item_item_id", "supplier_catalog_item", ["item_id"])
    op.create_index(
        "ix_catalog_item_group", "supplier_catalog_item", ["catalog_id", "category_id"]
    )
    op.create_index(
        "ix_catalog_item_included", "supplier_catalog_item", ["catalog_id", "included"]
    )

    op.create_table(
        "code_sequence",
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), primary_key=True),
        sa.Column("prefix", sa.String(8), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_table("code_sequence")
    op.drop_index("ix_catalog_item_included", table_name="supplier_catalog_item")
    op.drop_index("ix_catalog_item_group", table_name="supplier_catalog_item")
    op.drop_index("ix_supplier_catalog_item_item_id", table_name="supplier_catalog_item")
    op.drop_index("ix_supplier_catalog_item_catalog_id", table_name="supplier_catalog_item")
    op.drop_index("ix_supplier_catalog_item_tenant_id", table_name="supplier_catalog_item")
    op.drop_table("supplier_catalog_item")
    op.drop_index("ix_supplier_catalog_supplier_party_id", table_name="supplier_catalog")
    op.drop_index("ix_supplier_catalog_tenant_id", table_name="supplier_catalog")
    op.drop_table("supplier_catalog")
    op.drop_column("tenant", "catalog_group_create_policy")
    op.drop_column("tenant", "catalog_code_prefix")
    op.drop_column("tenant", "ext_supplier_catalog")

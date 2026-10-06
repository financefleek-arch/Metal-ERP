"""Item codes become one firm-wide running number (no group code, no provisional/locked state).

Not in production, so nothing is preserved: every existing product is renumbered 100001, 100002...
per firm in creation order (with the firm's optional prefix in front), the catalog rows, the
promoted items (barcode, sku) and the counter follow, and the now-pointless columns are dropped:

  - item_category.code_prefix (+ its unique index)   group code
  - catalog_product.code_locked                       provisional/locked state
  - supplier_catalog.code_prefix                      per-catalog fallback prefix

Old codes contain a hyphen and new ones never do, so a code can never collide mid-update.
Tally items already pushed under old part numbers are not updated here; they are re-sent.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

START = 100001


def upgrade() -> None:
    # 1. renumber products per firm, in creation order
    op.execute(
        f"""
        UPDATE catalog_product p
           SET code = COALESCE(t.catalog_code_prefix, '') || CAST(n.rn + {START - 1} AS VARCHAR)
          FROM (
                SELECT id, tenant_id,
                       ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY created_at, code, id) AS rn
                  FROM catalog_product
               ) n
          JOIN tenant t ON t.id = n.tenant_id
         WHERE p.id = n.id
        """
    )
    # 2. everything that copied the old code follows
    op.execute(
        """
        UPDATE supplier_catalog_item i
           SET code = p.code
          FROM catalog_product p
         WHERE i.product_id = p.id
        """
    )
    op.execute(
        """
        UPDATE item
           SET barcode = p.code, sku = p.code
          FROM catalog_product p
         WHERE p.item_id = item.id
        """
    )
    # 3. one counter per firm, empty prefix key
    op.execute("DELETE FROM code_sequence")
    op.execute(
        f"""
        INSERT INTO code_sequence (tenant_id, prefix, next_value)
        SELECT tenant_id, '', {START} + COUNT(*)
          FROM catalog_product
         GROUP BY tenant_id
        """
    )
    # 4. drop what the old scheme needed
    op.drop_index("uq_item_category_tenant_code_prefix", table_name="item_category")
    op.drop_column("item_category", "code_prefix")
    op.drop_column("catalog_product", "code_locked")
    op.drop_column("supplier_catalog", "code_prefix")


def downgrade() -> None:
    op.add_column(
        "supplier_catalog",
        sa.Column("code_prefix", sa.String(8), nullable=False, server_default="GEN"),
    )
    op.add_column(
        "catalog_product",
        sa.Column("code_locked", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("item_category", sa.Column("code_prefix", sa.String(8), nullable=True))
    op.create_index(
        "uq_item_category_tenant_code_prefix",
        "item_category",
        ["tenant_id", "code_prefix"],
        unique=True,
        postgresql_where=sa.text("code_prefix IS NOT NULL"),
    )
    # Codes stay as renumbered: there is no way back to the old ones.

"""Supplier Catalog: product registry + group codes.

The item code now belongs to the *product*, not to a catalog row, so the same product
offered in a later PDF reuses its code, group and name.

  - item_category.code_prefix   short code that starts item codes issued in a group (BM-0042)
  - catalog_product             the shop's product: (supplier, supplier code) -> our code
  - supplier_catalog_item.product_id / suggested_product_id
  - the firm-wide unique code on catalog rows becomes unique per catalog (the firm-wide
    uniqueness moves to catalog_product)

Existing catalog rows are backfilled with one product each; their codes are already in use,
so they are marked locked and never change.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-03
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip()


def backfill_products(bind: sa.engine.Connection) -> int:
    """One locked product per existing catalog row (their codes are already in use).

    Standalone so it can be tested against sample legacy data. Returns the number created.
    """
    rows = bind.execute(
        sa.text(
            "SELECT i.id, i.tenant_id, c.supplier_party_id, i.supplier_code, i.code, "
            "i.category_id, i.display_name, i.item_id "
            "FROM supplier_catalog_item i JOIN supplier_catalog c ON c.id = i.catalog_id "
            "ORDER BY i.created_at, i.code, i.id"
        )
    ).fetchall()
    products: list[dict[str, object]] = []
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for r in rows:
        key = (r.tenant_id, r.supplier_party_id or "", r.supplier_code)
        # a known supplier with the same supplier code in two old catalogs: only the first
        # keeps the supplier key (the unique index allows one product per supplier + code)
        supplier = r.supplier_party_id if key not in seen else None
        seen.add(key)
        pid = str(uuid.uuid4())
        products.append(
            {
                "id": pid, "t": r.tenant_id, "sp": supplier, "sc": r.supplier_code,
                "code": r.code, "cat": r.category_id, "name": r.display_name,
                "norm": _norm(r.display_name), "item": r.item_id,
            }
        )
        links.append({"pid": pid, "iid": r.id})
    if products:
        bind.execute(
            sa.text(
                "INSERT INTO catalog_product (id, tenant_id, supplier_party_id, supplier_code, "
                "code, code_locked, category_id, display_name, name_normalized, item_id) "
                "VALUES (:id, :t, :sp, :sc, :code, :locked, :cat, :name, :norm, :item)"
            ),
            [{**p, "locked": True} for p in products],
        )
        bind.execute(
            sa.text("UPDATE supplier_catalog_item SET product_id = :pid WHERE id = :iid"), links
        )
    return len(products)


def upgrade() -> None:
    op.add_column("item_category", sa.Column("code_prefix", sa.String(8), nullable=True))
    op.create_index(
        "uq_item_category_tenant_code_prefix",
        "item_category",
        ["tenant_id", "code_prefix"],
        unique=True,
        postgresql_where=sa.text("code_prefix IS NOT NULL"),
    )

    op.create_table(
        "catalog_product",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("supplier_party_id", sa.String(36), sa.ForeignKey("party.id")),
        sa.Column("supplier_code", sa.String(60), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("code_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("item_category.id")),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("name_normalized", sa.String(300), nullable=False, server_default=""),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "code", name="uq_catalog_product_tenant_code"),
    )
    op.create_index("ix_catalog_product_tenant_id", "catalog_product", ["tenant_id"])
    op.create_index("ix_catalog_product_item_id", "catalog_product", ["item_id"])
    op.create_index("ix_catalog_product_name", "catalog_product", ["tenant_id", "name_normalized"])
    op.create_index(
        "uq_catalog_product_supplier_code",
        "catalog_product",
        ["tenant_id", "supplier_party_id", "supplier_code"],
        unique=True,
        postgresql_where=sa.text("supplier_party_id IS NOT NULL"),
    )

    op.add_column(
        "supplier_catalog_item",
        sa.Column("product_id", sa.String(36), sa.ForeignKey("catalog_product.id"), nullable=True),
    )
    op.add_column(
        "supplier_catalog_item",
        sa.Column(
            "suggested_product_id", sa.String(36), sa.ForeignKey("catalog_product.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_supplier_catalog_item_product_id", "supplier_catalog_item", ["product_id"])

    if not context.is_offline_mode():  # `alembic --sql` only renders DDL
        backfill_products(op.get_bind())

    op.drop_constraint("uq_catalog_item_tenant_code", "supplier_catalog_item", type_="unique")
    op.create_unique_constraint(
        "uq_catalog_item_catalog_code", "supplier_catalog_item", ["catalog_id", "code"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_catalog_item_catalog_code", "supplier_catalog_item", type_="unique")
    op.create_unique_constraint(
        "uq_catalog_item_tenant_code", "supplier_catalog_item", ["tenant_id", "code"]
    )
    op.drop_index("ix_supplier_catalog_item_product_id", table_name="supplier_catalog_item")
    op.drop_column("supplier_catalog_item", "suggested_product_id")
    op.drop_column("supplier_catalog_item", "product_id")
    op.drop_index("uq_catalog_product_supplier_code", table_name="catalog_product")
    op.drop_index("ix_catalog_product_name", table_name="catalog_product")
    op.drop_index("ix_catalog_product_item_id", table_name="catalog_product")
    op.drop_index("ix_catalog_product_tenant_id", table_name="catalog_product")
    op.drop_table("catalog_product")
    op.drop_index("uq_item_category_tenant_code_prefix", table_name="item_category")
    op.drop_column("item_category", "code_prefix")

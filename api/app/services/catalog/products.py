"""The shop's product registry.

A *product* is what the shop sells; a catalog row is one supplier's offer of it. The code belongs
to the product, so a later PDF that offers the same thing (same supplier, same supplier code)
reuses the product and its code, group and name instead of getting new ones.

The code is a firm-wide running number (see `codes.py`) that never changes: the group is a
separate field, so moving a product to another group touches no code.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.normalize import normalize_name
from app.models import CatalogProduct, ItemCategory, SupplierCatalogItem, Tenant
from app.services.catalog.codes import allocate_codes

_CHUNK = 500


def issue_codes(session: Session, tenant: Tenant, count: int) -> list[str]:
    """`count` fresh codes, in order."""
    return allocate_codes(session, tenant, count)


def products_by_supplier_code(
    session: Session, tenant_id: str, supplier_party_id: str
) -> dict[str, CatalogProduct]:
    rows = session.scalars(
        select(CatalogProduct).where(
            CatalogProduct.tenant_id == tenant_id,
            CatalogProduct.supplier_party_id == supplier_party_id,
        )
    )
    return {p.supplier_code: p for p in rows}


def new_product(
    *,
    tenant_id: str,
    supplier_party_id: str | None,
    supplier_code: str,
    code: str,
    category_id: str | None,
    display_name: str,
) -> CatalogProduct:
    return CatalogProduct(
        tenant_id=tenant_id,
        supplier_party_id=supplier_party_id,
        supplier_code=supplier_code,
        code=code,
        category_id=category_id,
        display_name=display_name,
        name_normalized=normalize_name(display_name),
    )


def rename_product(product: CatalogProduct, display_name: str) -> None:
    product.display_name = display_name
    product.name_normalized = normalize_name(display_name)


def suggest_by_name(
    session: Session, tenant_id: str, wanted: dict[str, str], exclude_ids: set[str]
) -> dict[str, CatalogProduct]:
    """For each key -> normalised name in `wanted`, an existing product (not in `exclude_ids`)
    with the same normalised name: the likely same product from another supplier or file."""
    norms = {n for n in wanted.values() if n}
    found: dict[str, CatalogProduct] = {}
    for i in range(0, len(norms), _CHUNK):
        chunk = list(norms)[i : i + _CHUNK]
        for p in session.scalars(
            select(CatalogProduct)
            .where(CatalogProduct.tenant_id == tenant_id, CatalogProduct.name_normalized.in_(chunk))
            .order_by(CatalogProduct.created_at, CatalogProduct.id)
        ):
            if p.id not in exclude_ids:
                found.setdefault(p.name_normalized, p)
    return {key: found[n] for key, n in wanted.items() if n in found}


def regroup(
    session: Session,
    products: Sequence[CatalogProduct],
    category: ItemCategory | None,
) -> None:
    """Move products to `category` (None = no group). Codes are untouched; the catalog rows
    follow the product."""
    if not products:
        return
    cat_id = category.id if category else None
    for product in products:
        product.category_id = cat_id
    session.flush()
    ids = [p.id for p in products]
    for i in range(0, len(ids), _CHUNK):
        session.execute(
            update(SupplierCatalogItem)
            .where(SupplierCatalogItem.product_id.in_(ids[i : i + _CHUNK]))
            .values(category_id=cat_id, suggested_group=None)
        )

"""A supplier is a party (role supplier or both). These helpers answer "what do we hold from
this supplier?" for the party page, and guard deleting or demoting a supplier that catalogs
still point at."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CatalogProduct, SupplierCatalog


@dataclass
class SupplierSummary:
    catalogs: list[SupplierCatalog]
    product_count: int
    promoted_count: int  # products already in the item list


def catalog_ref_count(session: Session, party_id: str) -> int:
    """Catalogs and products that reference this party as their supplier."""
    cats = session.scalar(
        select(func.count()).select_from(SupplierCatalog).where(
            SupplierCatalog.supplier_party_id == party_id
        )
    )
    prods = session.scalar(
        select(func.count()).select_from(CatalogProduct).where(
            CatalogProduct.supplier_party_id == party_id
        )
    )
    return int(cats or 0) + int(prods or 0)


def supplier_summary(session: Session, tenant_id: str, party_id: str) -> SupplierSummary:
    catalogs = list(
        session.scalars(
            select(SupplierCatalog)
            .where(
                SupplierCatalog.tenant_id == tenant_id,
                SupplierCatalog.supplier_party_id == party_id,
            )
            .order_by(SupplierCatalog.created_at.desc())
        )
    )
    products = session.scalar(
        select(func.count()).select_from(CatalogProduct).where(
            CatalogProduct.tenant_id == tenant_id, CatalogProduct.supplier_party_id == party_id
        )
    )
    promoted = session.scalar(
        select(func.count()).select_from(CatalogProduct).where(
            CatalogProduct.tenant_id == tenant_id,
            CatalogProduct.supplier_party_id == party_id,
            CatalogProduct.item_id.is_not(None),
        )
    )
    return SupplierSummary(catalogs, int(products or 0), int(promoted or 0))

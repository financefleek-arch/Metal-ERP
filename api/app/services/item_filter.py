"""Which items: one description (`ItemFilter`) shared by the item list, the counts, every bulk
action and the customer catalog builder."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import CatalogProduct, Item, SupplierCatalogItem
from app.models._mixins import ItemStatus
from app.schemas_item import MAX_BULK_FILTER, ItemFilter
from app.services.items import apply_search


def filter_clauses(f: ItemFilter) -> list:  # type: ignore[type-arg]
    """The `where` clauses for a filter (search text is applied separately)."""
    out: list = []  # type: ignore[type-arg]
    if f.status is None:
        out.append(Item.status != ItemStatus.archived)
    else:
        out.append(Item.status == f.status)
    if f.type is not None:
        out.append(Item.item_type == f.type)
    if f.no_hsn:
        out.append(Item.hsn_code.is_(None))
    if f.price_review:
        out.append(Item.price_review_pending.is_(True))
    if f.availability:
        out.append(Item.availability.in_([a.value for a in f.availability]))
    if f.no_photo:
        out.append(Item.primary_media_id.is_(None))
    if f.in_tally is True:
        out.append(or_(Item.tally_guid.is_not(None), Item.tally_status == "synced"))
    elif f.in_tally is False:
        out.append(and_(Item.tally_guid.is_(None), Item.tally_status != "synced"))
    if f.tally_price_due:
        # sent before, but Tally's price is not the item's rate now (changed since, or Tally
        # dropped it)
        out.append(
            and_(
                Item.tally_status == "synced",
                Item.default_rate.is_not(None),
                or_(
                    Item.tally_price.is_(None),
                    Item.tally_price != func.round(Item.default_rate, 2),
                    and_(
                        Item.tally_seen_price.is_not(None),
                        Item.tally_seen_price != Item.tally_price,
                    ),
                ),
            )
        )
    if f.supplier_id:
        out.append(
            exists().where(
                CatalogProduct.item_id == Item.id,
                CatalogProduct.supplier_party_id == f.supplier_id,
            )
        )
    if f.catalog_id:
        out.append(
            exists().where(
                SupplierCatalogItem.item_id == Item.id,
                SupplierCatalogItem.catalog_id == f.catalog_id,
            )
        )
    if f.group_id:
        out.append(Item.group_id == f.group_id)
    if f.category_id:
        out.append(Item.category_id == f.category_id)
    elif f.uncategorised:
        out.append(Item.category_id.is_(None))
    if f.ungrouped:
        out.append(Item.group_id.is_(None))
    return out


def filter_stmt(session: Session, tenant_id: str, f: ItemFilter):  # type: ignore[no-untyped-def]
    stmt = select(Item).where(
        Item.tenant_id == tenant_id, Item.merged_into_id.is_(None), *filter_clauses(f)
    )
    if f.q and f.q.strip():
        stmt = apply_search(stmt, session, f.q, tenant_id=tenant_id)
    return stmt


def resolve_ids(
    session: Session, tenant_id: str, ids: list[str] | None, f: ItemFilter | None
) -> list[str]:
    """The ids a bulk action covers: the ticked ones, or everything matching the filter."""
    if ids is not None:
        return ids
    assert f is not None
    found = list(session.scalars(filter_stmt(session, tenant_id, f)).unique().all())
    if len(found) > MAX_BULK_FILTER:
        raise HTTPException(
            status_code=422,
            detail=f"That matches {len(found)} items. Narrow it to {MAX_BULK_FILTER} or fewer.",
        )
    return [it.id for it in found]



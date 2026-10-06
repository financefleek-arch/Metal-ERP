"""Per-tenant item categories — the top bucket of category → group → item.

For a bartan shop these are brands (Hawkins, Mintage, ST, GS); for a
metal-bar shop, materials (Steel, Aluminium). Seeded on register.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import CatalogProduct, Item, ItemCategory, ProductGroup, SupplierCatalogItem
from app.schemas_catalogue import (
    CategoryDeleteIn,
    CategoryIn,
    CategoryMergeIn,
    CategoryOut,
    CategoryUpdate,
)
from app.services import audit
from app.services.items import hsn_gst_rate

router = APIRouter(prefix="/api/item-categories", tags=["item-categories"])


def _counts(session: SessionDep, tenant_id: str) -> dict[str, tuple[int, int]]:
    g: dict[str, int] = {
        cid: n
        for cid, n in session.execute(
            select(ProductGroup.category_id, func.count())
            .where(ProductGroup.tenant_id == tenant_id, ProductGroup.category_id.is_not(None))
            .group_by(ProductGroup.category_id)
        ).all()
        if cid is not None
    }
    i: dict[str, int] = {
        cid: n
        for cid, n in session.execute(
            select(Item.category_id, func.count())
            .where(Item.tenant_id == tenant_id, Item.category_id.is_not(None))
            .group_by(Item.category_id)
        ).all()
        if cid is not None
    }
    return {cid: (g.get(cid, 0), i.get(cid, 0)) for cid in set(g) | set(i)}


def _out(session: SessionDep, tenant_id: str, c: ItemCategory) -> CategoryOut:
    groups, items = _counts(session, tenant_id).get(c.id, (0, 0))
    missing = session.scalar(
        select(func.count()).select_from(Item).where(
            Item.tenant_id == tenant_id,
            Item.category_id == c.id,
            Item.hsn_code.is_(None),
            Item.merged_into_id.is_(None),
        )
    )
    return CategoryOut(
        id=c.id,
        name=c.name,
        sort=c.sort,
        hsn_code=c.hsn_code,
        gst_rate=c.gst_rate,
        group_count=groups,
        item_count=items,
        items_without_hsn=int(missing or 0),
    )


def _owned(session: SessionDep, tenant_id: str, cat_id: str) -> ItemCategory:
    c = session.scalar(
        select(ItemCategory).where(
            ItemCategory.id == cat_id, ItemCategory.tenant_id == tenant_id
        )
    )
    if c is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return c


@router.get("", response_model=list[CategoryOut])
def list_categories(user: CurrentUser, session: SessionDep) -> list[CategoryOut]:
    cats = list(
        session.scalars(
            select(ItemCategory)
            .where(ItemCategory.tenant_id == user.tenant_id)
            .order_by(ItemCategory.sort, func.lower(ItemCategory.name))
        ).all()
    )
    counts = _counts(session, user.tenant_id)
    missing: dict[str, int] = {
        cid: n
        for cid, n in session.execute(
            select(Item.category_id, func.count())
            .where(
                Item.tenant_id == user.tenant_id,
                Item.category_id.is_not(None),
                Item.hsn_code.is_(None),
                Item.merged_into_id.is_(None),
            )
            .group_by(Item.category_id)
        ).all()
        if cid is not None
    }
    return [
        CategoryOut(
            id=c.id,
            name=c.name,
            sort=c.sort,
            hsn_code=c.hsn_code,
            gst_rate=c.gst_rate,
            group_count=counts.get(c.id, (0, 0))[0],
            item_count=counts.get(c.id, (0, 0))[1],
            items_without_hsn=missing.get(c.id, 0),
        )
        for c in cats
    ]


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, user: WriteUser, session: SessionDep) -> CategoryOut:
    dupe = session.scalar(
        select(ItemCategory).where(
            ItemCategory.tenant_id == user.tenant_id,
            func.lower(ItemCategory.name) == body.name.lower().strip(),
        )
    )
    if dupe is not None:
        raise HTTPException(status_code=409, detail=f"Category '{body.name}' already exists")
    c = ItemCategory(
        tenant_id=user.tenant_id,
        name=body.name.strip(),
        sort=body.sort,
        hsn_code=body.hsn_code,
        gst_rate=body.gst_rate,
    )
    session.add(c)
    session.flush()
    return _out(session, user.tenant_id, c)


@router.patch("/{cat_id}", response_model=CategoryOut)
def update_category(
    cat_id: str, body: CategoryUpdate, user: WriteUser, session: SessionDep
) -> CategoryOut:
    c = _owned(session, user.tenant_id, cat_id)
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch:
        clash = session.scalar(
            select(ItemCategory).where(
                ItemCategory.tenant_id == user.tenant_id,
                ItemCategory.id != c.id,
                func.lower(ItemCategory.name) == patch["name"].lower().strip(),
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Category '{patch['name']}' already exists",
            )
        c.name = patch["name"].strip()
    if "sort" in patch:
        c.sort = patch["sort"]
    if "hsn_code" in patch:
        c.hsn_code = patch["hsn_code"] or None
    if "gst_rate" in patch:
        c.gst_rate = patch["gst_rate"]
    session.flush()
    return _out(session, user.tenant_id, c)


@router.post("/{cat_id}/apply-hsn")
def apply_hsn(cat_id: str, user: WriteUser, session: SessionDep) -> dict[str, int]:
    """Give the items in this group that have no HSN the group's HSN (and GST rate). Items that
    already have an HSN are never changed."""
    c = _owned(session, user.tenant_id, cat_id)
    if not c.hsn_code:
        raise HTTPException(status_code=422, detail="Set an HSN for this group first.")
    rate = c.gst_rate if c.gst_rate is not None else hsn_gst_rate(session, c.hsn_code)
    items = list(
        session.scalars(
            select(Item).where(
                Item.tenant_id == user.tenant_id,
                Item.category_id == c.id,
                Item.hsn_code.is_(None),
                Item.merged_into_id.is_(None),
            )
        )
    )
    for it in items:
        it.hsn_code = c.hsn_code
        if rate is not None:
            it.gst_rate = float(rate)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="item_category",
        entity_id=c.id,
        action="apply_hsn",
        after={"hsn": c.hsn_code, "updated": len(items)},
    )
    session.flush()
    return {"updated": len(items)}


def _repoint(session: SessionDep, tenant_id: str, src_id: str, target: str | None) -> None:
    """Move everything that points at category `src_id` to `target` (None = detach):
    product groups, items, and supplier-catalog items.
    """
    for model in (ProductGroup, Item, SupplierCatalogItem, CatalogProduct):
        session.execute(
            update(model)
            .where(model.tenant_id == tenant_id, model.category_id == src_id)
            .values(category_id=target)
        )


@router.post("/{cat_id}/merge", response_model=CategoryOut)
def merge_category(
    cat_id: str, body: CategoryMergeIn, user: WriteUser, session: SessionDep
) -> CategoryOut:
    """Merge category `cat_id` into `body.into`, then delete `cat_id`."""
    src = _owned(session, user.tenant_id, cat_id)
    if body.into == src.id:
        raise HTTPException(status_code=422, detail="Pick a different category to merge into.")
    dst = _owned(session, user.tenant_id, body.into)
    _repoint(session, user.tenant_id, src.id, dst.id)
    session.delete(src)
    session.flush()
    return _out(session, user.tenant_id, dst)


@router.delete("/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    cat_id: str, body: CategoryDeleteIn, user: WriteUser, session: SessionDep
) -> None:
    c = _owned(session, user.tenant_id, cat_id)
    # reassign_to None => detach (set null) rather than block: a category can always be dropped.
    target: str | None = None
    if body.reassign_to is not None:
        _owned(session, user.tenant_id, body.reassign_to)
        target = body.reassign_to
    _repoint(session, user.tenant_id, c.id, target)
    session.delete(c)

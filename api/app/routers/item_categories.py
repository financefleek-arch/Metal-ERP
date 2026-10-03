"""Per-tenant item categories — the top bucket of category → group → item.

For a bartan shop these are brands (Hawkins, Mintage, ST, GS); for a
metal-bar shop, materials (Steel, Aluminium). Seeded on register.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import CatalogProduct, Item, ItemCategory, ProductGroup, SupplierCatalogItem, Tenant
from app.schemas_catalogue import (
    CategoryDeleteIn,
    CategoryIn,
    CategoryMergeIn,
    CategoryOut,
    CategoryUpdate,
)
from app.services.catalog.codes import normalize_prefix
from app.services.catalog.products import fallback_prefix, prefix_in_use

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
    return [
        CategoryOut(
            id=c.id,
            name=c.name,
            sort=c.sort,
            code_prefix=c.code_prefix,
            group_count=counts.get(c.id, (0, 0))[0],
            item_count=counts.get(c.id, (0, 0))[1],
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
    c = ItemCategory(tenant_id=user.tenant_id, name=body.name.strip(), sort=body.sort)
    session.add(c)
    session.flush()
    return CategoryOut(id=c.id, name=c.name, sort=c.sort, code_prefix=c.code_prefix)


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
    if patch.get("code_prefix") is not None:
        _set_code_prefix(session, user.tenant_id, c, patch["code_prefix"])
    session.flush()
    counts = _counts(session, user.tenant_id).get(c.id, (0, 0))
    return CategoryOut(
        id=c.id, name=c.name, sort=c.sort, code_prefix=c.code_prefix,
        group_count=counts[0], item_count=counts[1],
    )


def _set_code_prefix(session: SessionDep, tenant_id: str, c: ItemCategory, raw: str) -> None:
    prefix = normalize_prefix(raw)
    if prefix is None or len(prefix) > 4:
        raise HTTPException(status_code=422, detail="Group code must be 2 to 4 letters or digits.")
    if prefix == c.code_prefix:
        return
    if c.code_prefix and prefix_in_use(session, tenant_id, c.code_prefix):
        raise HTTPException(
            status_code=409,
            detail=f"Codes like {c.code_prefix}-0001 already exist, so this group's code is fixed.",
        )
    tenant = session.get(Tenant, tenant_id)
    assert tenant is not None
    clash = session.scalar(
        select(ItemCategory.id).where(
            ItemCategory.tenant_id == tenant_id,
            ItemCategory.id != c.id,
            ItemCategory.code_prefix == prefix,
        )
    )
    taken = prefix == fallback_prefix(tenant) or prefix_in_use(session, tenant_id, prefix)
    if clash is not None or taken:
        raise HTTPException(status_code=409, detail=f"The code {prefix} is already taken.")
    c.code_prefix = prefix


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
    counts = _counts(session, user.tenant_id).get(dst.id, (0, 0))
    return CategoryOut(
        id=dst.id, name=dst.name, sort=dst.sort, code_prefix=dst.code_prefix,
        group_count=counts[0], item_count=counts[1],
    )


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

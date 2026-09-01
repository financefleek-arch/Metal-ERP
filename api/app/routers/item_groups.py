"""Product groups — the middle level of category → group → item.

The table has existed since 0001 (dormant); this surfaces it. A group
carries the shared attributes (category, HSN, UOM, item_type, default
rate_mode); its leaves inherit unless they override.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from app.deps import CurrentUser, SessionDep, WriteUser
from app.domain.normalize import load_synonym_map, normalize_name
from app.domain.product_parse import generated_name
from app.models import Item, ItemAlias, ItemCategory, ProductGroup
from app.schemas_catalogue import (
    GroupDetail,
    GroupIn,
    GroupLeaf,
    GroupOut,
    GroupUpdate,
    SizeOrderIn,
)

router = APIRouter(prefix="/api/item-groups", tags=["item-groups"])


def _owned(session: SessionDep, tenant_id: str, gid: str) -> ProductGroup:
    g = session.scalar(
        select(ProductGroup).where(
            ProductGroup.id == gid, ProductGroup.tenant_id == tenant_id
        )
    )
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return g


def _cat_name(session: SessionDep, cid: str | None) -> str | None:
    if not cid:
        return None
    c = session.get(ItemCategory, cid)
    return c.name if c else None


def _leaf_out(session: SessionDep, it: Item, group: ProductGroup) -> GroupLeaf:
    cat_name = _cat_name(session, it.category_id or group.category_id)
    return GroupLeaf(
        id=it.id,
        size_pos=it.size_pos,
        size_label=it.size_label,
        size_text=it.size_text,
        sku=it.sku,
        rate_mode=it.rate_mode,
        weight_per_piece=it.weight_per_piece,
        default_rate=it.default_rate,
        last_rate=it.last_rate,
        last_sold_at=it.last_sold_at.isoformat() if it.last_sold_at else None,
        generated_name=generated_name(
            category_name=cat_name,
            group_name=group.name,
            sku=it.sku,
            size_label=it.size_label or it.size_text,
        ),
    )


def _group_out(session: SessionDep, g: ProductGroup) -> GroupOut:
    n = session.scalar(
        select(func.count()).select_from(Item).where(
            Item.group_id == g.id, Item.merged_into_id.is_(None)
        )
    )
    return GroupOut(
        id=g.id,
        name=g.name,
        name_normalized=g.name_normalized,
        category_id=g.category_id,
        category_name=_cat_name(session, g.category_id),
        hsn_code=g.hsn_code,
        uom=g.uom,
        item_type=g.item_type,
        default_rate_mode=g.default_rate_mode,
        item_count=n or 0,
    )


@router.get("", response_model=list[GroupOut])
def list_groups(
    user: CurrentUser,
    session: SessionDep,
    category_id: str | None = Query(default=None),
) -> list[GroupOut]:
    stmt = select(ProductGroup).where(ProductGroup.tenant_id == user.tenant_id)
    if category_id:
        stmt = stmt.where(ProductGroup.category_id == category_id)
    stmt = stmt.order_by(func.lower(ProductGroup.name))
    return [_group_out(session, g) for g in session.scalars(stmt).all()]


@router.post("", response_model=GroupDetail, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupIn, user: WriteUser, session: SessionDep) -> GroupDetail:
    key = normalize_name(body.name, load_synonym_map(session, user.tenant_id))
    if not key:
        raise HTTPException(status_code=422, detail="Group name normalises to nothing")
    dupe = session.scalar(
        select(ProductGroup).where(
            ProductGroup.tenant_id == user.tenant_id,
            ProductGroup.name_normalized == key,
        )
    )
    if dupe is not None:
        raise HTTPException(status_code=409, detail=f"A group '{dupe.name}' already exists")
    if body.category_id:
        c = session.get(ItemCategory, body.category_id)
        if c is None or c.tenant_id != user.tenant_id:
            raise HTTPException(status_code=422, detail="Unknown category")

    g = ProductGroup(
        tenant_id=user.tenant_id,
        name=body.name.strip(),
        name_normalized=key,
        category_id=body.category_id,
        hsn_code=body.hsn_code,
        uom=body.uom,
        item_type=body.item_type,
        default_rate_mode=body.default_rate_mode,
    )
    session.add(g)
    session.flush()
    return GroupDetail(**_group_out(session, g).model_dump(), leaves=[])


@router.get("/{gid}", response_model=GroupDetail)
def get_group(gid: str, user: CurrentUser, session: SessionDep) -> GroupDetail:
    g = _owned(session, user.tenant_id, gid)
    leaves = list(
        session.scalars(
            select(Item)
            .where(Item.group_id == g.id, Item.merged_into_id.is_(None))
            .order_by(Item.size_pos.nulls_last(), func.lower(Item.name))
        ).all()
    )
    return GroupDetail(
        **_group_out(session, g).model_dump(),
        leaves=[_leaf_out(session, it, g) for it in leaves],
    )


@router.patch("/{gid}", response_model=GroupDetail)
def update_group(
    gid: str, body: GroupUpdate, user: WriteUser, session: SessionDep
) -> GroupDetail:
    g = _owned(session, user.tenant_id, gid)
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch:
        key = normalize_name(patch["name"], load_synonym_map(session, user.tenant_id))
        clash = session.scalar(
            select(ProductGroup).where(
                ProductGroup.tenant_id == user.tenant_id,
                ProductGroup.id != g.id,
                ProductGroup.name_normalized == key,
            )
        )
        if clash is not None:
            raise HTTPException(status_code=409, detail=f"A group '{clash.name}' already exists")
        g.name = patch["name"].strip()
        g.name_normalized = key
    for field_ in ("category_id", "hsn_code", "uom", "item_type", "default_rate_mode"):
        if field_ in patch:
            setattr(g, field_, patch[field_])
    session.flush()
    return get_group(gid, user, session)


@router.patch("/{gid}/size-order", response_model=GroupDetail)
def set_size_order(
    gid: str, body: SizeOrderIn, user: WriteUser, session: SessionDep
) -> GroupDetail:
    g = _owned(session, user.tenant_id, gid)
    leaves = {
        it.id: it
        for it in session.scalars(
            select(Item).where(Item.group_id == g.id, Item.merged_into_id.is_(None))
        ).all()
    }
    for pos, leaf_id in enumerate(body.leaf_ids, start=1):
        it = leaves.get(leaf_id)
        if it is not None:
            it.size_pos = pos
    session.flush()
    return get_group(gid, user, session)


@router.delete("/{gid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(gid: str, user: WriteUser, session: SessionDep) -> None:
    g = _owned(session, user.tenant_id, gid)
    n = session.scalar(
        select(func.count()).select_from(Item).where(
            Item.group_id == g.id, Item.merged_into_id.is_(None)
        )
    )
    if n:
        # Detach the leaves (they become loose items), then drop the group.
        session.execute(
            sa_update(Item).where(Item.group_id == g.id).values(group_id=None, size_pos=None)
        )
    # group-scoped aliases go too
    session.execute(sa_delete(ItemAlias).where(ItemAlias.group_id == g.id))
    session.delete(g)

"""Item catalogue CRUD, scoped to the caller's tenant.

Resolution (search / dedupe) reuses `domain.normalize` + the shared
`item_resolution` ladder. Everything created here starts UNCONFIRMED — a
hand-made item still passes through the review queue once.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Item, ItemAlias, ItemCategory, ProductGroup
from app.models._mixins import ItemSource, ItemStatus, ItemType
from app.schemas_item import (
    ItemCreate,
    ItemListItem,
    ItemMergeIn,
    ItemOut,
    ItemUpdate,
)
from app.services.items import (
    apply_search,
    document_count,
    hsn_gst_rate,
    rate_in_band,
)

router = APIRouter(prefix="/api/items", tags=["items"])


def _owned(session: SessionDep, tenant_id: str, item_id: str) -> Item:
    it = session.scalar(
        select(Item).where(Item.id == item_id, Item.tenant_id == tenant_id)
    )
    if it is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return it


def _list_item(it: Item) -> ItemListItem:
    return ItemListItem.model_validate(it)


def _out(session: SessionDep, it: Item) -> ItemOut:
    out = ItemOut.model_validate(it)
    out.rate_in_band = rate_in_band(it)
    out.document_count = document_count(session, it.id)
    return out


def _normalized(session: SessionDep, tenant_id: str, name: str) -> str:
    return normalize_name(name, load_synonym_map(session, tenant_id))


@router.get("", response_model=list[ItemListItem])
def list_items(
    user: CurrentUser,
    session: SessionDep,
    q: str | None = Query(default=None, description="fuzzy name / grade / size / HSN"),
    type_: ItemType | None = Query(default=None, alias="type"),
    status_: ItemStatus | None = Query(default=None, alias="status"),
    no_hsn: bool = Query(default=False),
    price_review: bool = Query(default=False),
) -> list[ItemListItem]:
    stmt = select(Item).where(
        Item.tenant_id == user.tenant_id, Item.merged_into_id.is_(None)
    )
    if status_ is None:
        stmt = stmt.where(Item.status != ItemStatus.archived)
    else:
        stmt = stmt.where(Item.status == status_)
    if type_ is not None:
        stmt = stmt.where(Item.item_type == type_)
    if no_hsn:
        stmt = stmt.where(Item.hsn_code.is_(None))
    if price_review:
        stmt = stmt.where(Item.price_review_pending.is_(True))

    if q:
        stmt = apply_search(stmt, session, q)
    else:
        stmt = stmt.order_by(
            (Item.status == ItemStatus.confirmed).desc(), func.lower(Item.name)
        )

    return [_list_item(i) for i in session.scalars(stmt).unique().all()]


# --------------------------------------------------------------------------
# tree view: category -> group -> leaf, plus an "Ungrouped" bucket
# --------------------------------------------------------------------------


class TreeLeaf(BaseModel):
    id: str
    name: str
    size_label: str | None
    default_rate: str | None
    status: ItemStatus


class TreeGroup(BaseModel):
    id: str
    name: str
    item_type: ItemType
    leaves: list[TreeLeaf]


class TreeCategory(BaseModel):
    id: str | None
    name: str
    groups: list[TreeGroup]
    loose: list[TreeLeaf]  # leaves in this category with no group


@router.get("/tree", response_model=list[TreeCategory])
def item_tree(user: CurrentUser, session: SessionDep) -> list[TreeCategory]:
    cats = list(
        session.scalars(
            select(ItemCategory)
            .where(ItemCategory.tenant_id == user.tenant_id)
            .order_by(ItemCategory.sort, func.lower(ItemCategory.name))
        ).all()
    )
    groups = list(
        session.scalars(
            select(ProductGroup)
            .where(ProductGroup.tenant_id == user.tenant_id)
            .order_by(func.lower(ProductGroup.name))
        ).all()
    )
    items = list(
        session.scalars(
            select(Item).where(
                Item.tenant_id == user.tenant_id,
                Item.merged_into_id.is_(None),
                Item.status != ItemStatus.archived,
            )
        ).all()
    )

    def leaf(it: Item) -> TreeLeaf:
        return TreeLeaf(
            id=it.id,
            name=it.name,
            size_label=it.size_label or it.size_text,
            default_rate=str(it.default_rate) if it.default_rate is not None else None,
            status=it.status,
        )

    leaves_by_group: dict[str, list[Item]] = {}
    loose_by_cat: dict[str | None, list[Item]] = {}
    for it in items:
        if it.group_id:
            leaves_by_group.setdefault(it.group_id, []).append(it)
        else:
            loose_by_cat.setdefault(it.category_id, []).append(it)

    groups_by_cat: dict[str | None, list[ProductGroup]] = {}
    for g in groups:
        groups_by_cat.setdefault(g.category_id, []).append(g)

    def sort_leaves(lst: list[Item]) -> list[Item]:
        return sorted(
            lst,
            key=lambda x: (x.size_pos if x.size_pos is not None else 9999, x.name.lower()),
        )

    out: list[TreeCategory] = []
    for c in cats:
        out.append(
            TreeCategory(
                id=c.id,
                name=c.name,
                groups=[
                    TreeGroup(
                        id=g.id,
                        name=g.name,
                        item_type=g.item_type,
                        leaves=[leaf(x) for x in sort_leaves(leaves_by_group.get(g.id, []))],
                    )
                    for g in groups_by_cat.get(c.id, [])
                ],
                loose=[leaf(x) for x in sort_leaves(loose_by_cat.get(c.id, []))],
            )
        )
    # uncategorised groups + loose items
    unc_groups = groups_by_cat.get(None, [])
    unc_loose = loose_by_cat.get(None, [])
    if unc_groups or unc_loose:
        out.append(
            TreeCategory(
                id=None,
                name="Uncategorised",
                groups=[
                    TreeGroup(
                        id=g.id,
                        name=g.name,
                        item_type=g.item_type,
                        leaves=[leaf(x) for x in sort_leaves(leaves_by_group.get(g.id, []))],
                    )
                    for g in unc_groups
                ],
                loose=[leaf(x) for x in sort_leaves(unc_loose)],
            )
        )
    return out


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemCreate, user: WriteUser, session: SessionDep) -> ItemOut:
    key = _normalized(session, user.tenant_id, body.name)
    if not key:
        raise HTTPException(status_code=422, detail="Item name normalises to nothing")

    clash = session.scalar(
        select(Item).where(
            Item.tenant_id == user.tenant_id, Item.name_normalized == key
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Looks like an existing item: {clash.name}",
        )

    data = body.model_dump(exclude_none=True)
    it = Item(
        tenant_id=user.tenant_id,
        name_normalized=key,
        source=ItemSource.manual,
        status=ItemStatus.unconfirmed,
        **data,
    )
    _apply_group_inheritance(session, user.tenant_id, it, body.model_fields_set)
    # HSN -> GST rate (data is right the day GST turns on; not printed in M1).
    rate = hsn_gst_rate(session, it.hsn_code)
    if rate is not None:
        it.gst_rate = float(rate)
    session.add(it)
    session.flush()
    return _out(session, it)


def _apply_group_inheritance(
    session: SessionDep, tenant_id: str, it: Item, set_fields: set[str]
) -> None:
    """When a leaf is put in a group, fill from the group any field the caller
    did NOT explicitly set (rate_mode, category, hsn, uom, item_type).
    """
    if not it.group_id:
        return
    grp = session.scalar(
        select(ProductGroup).where(
            ProductGroup.id == it.group_id, ProductGroup.tenant_id == tenant_id
        )
    )
    if grp is None:
        raise HTTPException(status_code=422, detail="Unknown product group")
    if "rate_mode" not in set_fields:
        it.rate_mode = grp.default_rate_mode
    if "category_id" not in set_fields and it.category_id is None:
        it.category_id = grp.category_id
    if "hsn_code" not in set_fields and not it.hsn_code:
        it.hsn_code = grp.hsn_code
    if "uom" not in set_fields and not it.uom:
        it.uom = grp.uom
    if "item_type" not in set_fields:
        it.item_type = grp.item_type


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, user: CurrentUser, session: SessionDep) -> ItemOut:
    return _out(session, _owned(session, user.tenant_id, item_id))


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(
    item_id: str, body: ItemUpdate, user: WriteUser, session: SessionDep
) -> ItemOut:
    it = _owned(session, user.tenant_id, item_id)
    patch = body.model_dump(exclude_unset=True)

    if "name" in patch:
        key = _normalized(session, user.tenant_id, patch["name"])
        if not key:
            raise HTTPException(status_code=422, detail="Item name normalises to nothing")
        clash = session.scalar(
            select(Item).where(
                Item.tenant_id == user.tenant_id,
                Item.id != it.id,
                Item.name_normalized == key,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Looks like an existing item: {clash.name}",
            )
        it.name_normalized = key

    hsn_changed = "hsn_code" in patch and patch["hsn_code"] != it.hsn_code
    group_changed = "group_id" in patch and patch["group_id"] != it.group_id
    for field, value in patch.items():
        setattr(it, field, value)
    if group_changed:
        _apply_group_inheritance(session, user.tenant_id, it, set(patch.keys()))
    if hsn_changed:
        rate = hsn_gst_rate(session, it.hsn_code)
        if rate is not None:
            it.gst_rate = float(rate)

    session.flush()
    return _out(session, it)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str, user: WriteUser, session: SessionDep) -> None:
    it = _owned(session, user.tenant_id, item_id)
    refs = document_count(session, it.id)
    if it.times_billed > 0 or refs > 0:
        n = max(refs, it.times_billed)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{it.name}' is on {n} document{'s' if n != 1 else ''}. "
                "Archive it instead."
            ),
        )
    session.delete(it)


@router.post("/{item_id}/confirm", response_model=ItemOut)
def confirm_item(item_id: str, user: WriteUser, session: SessionDep) -> ItemOut:
    it = _owned(session, user.tenant_id, item_id)
    it.status = ItemStatus.confirmed
    session.flush()
    return _out(session, it)


@router.post("/{item_id}/merge", response_model=ItemOut)
def merge_item(
    item_id: str, body: ItemMergeIn, user: WriteUser, session: SessionDep
) -> ItemOut:
    """Fold `item_id` (loser) into `target_id` (winner): the loser's name
    becomes an alias of the winner, existing invoice lines repoint, the
    loser is hidden (merged_into_id set), never deleted.
    """
    if body.target_id == item_id:
        raise HTTPException(status_code=422, detail="Cannot merge an item into itself")
    loser = _owned(session, user.tenant_id, item_id)
    winner = _owned(session, user.tenant_id, body.target_id)
    if loser.merged_into_id is not None:
        raise HTTPException(status_code=409, detail="This item is already merged")

    # Keep the loser's wording as an alias so a future line with that text matches.
    alias_key = loser.name_normalized
    exists = session.scalar(
        select(ItemAlias).where(
            ItemAlias.tenant_id == user.tenant_id,
            ItemAlias.alias_normalized == alias_key,
        )
    )
    if exists is None and alias_key != winner.name_normalized:
        session.add(
            ItemAlias(
                tenant_id=user.tenant_id,
                item_id=winner.id,
                alias_text=loser.name,
                alias_normalized=alias_key,
            )
        )

    # Repoint any invoice lines (table may not exist yet — guard).
    il = Item.metadata.tables.get("invoice_line")
    if il is not None and "item_id" in il.c:
        session.execute(
            il.update().where(il.c.item_id == loser.id).values(item_id=winner.id)
        )

    loser.merged_into_id = winner.id
    loser.status = ItemStatus.archived
    winner.times_billed += loser.times_billed
    if loser.last_sold_at and (
        winner.last_sold_at is None or loser.last_sold_at > winner.last_sold_at
    ):
        winner.last_sold_at = loser.last_sold_at
    session.flush()
    return _out(session, winner)


class ResolveCandidate(ItemListItem):
    score: float


class ResolveResult(BaseModel):
    method: str | None
    confidence: float | None
    weak: bool
    candidates: list[ResolveCandidate]


@router.post("/resolve", response_model=ResolveResult)
def resolve(
    user: CurrentUser,
    session: SessionDep,
    description: str = Query(..., min_length=1),
    hsn: str | None = Query(default=None),
) -> ResolveResult:
    """Type-ahead helper for the invoice editor: what would this free text
    match? Returns the shared ladder's result (no LLM), with each candidate
    hydrated to full item fields so the editor can fill the line on pick.

    On an exact / alias hit the single matched item is returned as the only
    candidate (score 1.0 / 0.98). On a fuzzy pass the ranked candidates come
    back with their adjusted trigram score. SQLite (no pg_trgm): a non-exact,
    non-alias query returns `method=None, candidates=[]`.
    """
    from app.services.item_resolution import resolve_item

    m = resolve_item(session, user.tenant_id, description, hsn)

    # (item_id -> score) preserving ladder order: the resolved item first
    # (exact/alias), else the fuzzy candidates.
    scored: list[tuple[str, float]] = []
    if m.item_id is not None and not m.candidates:
        scored.append((m.item_id, m.confidence or 1.0))
    else:
        scored = [(c.item_id, round(c.adjusted_score, 3)) for c in m.candidates[:5]]

    items_by_id = {
        it.id: it
        for it in session.scalars(
            select(Item).where(Item.id.in_([sid for sid, _ in scored]))
        ).all()
    }
    candidates = [
        ResolveCandidate(**ItemListItem.model_validate(items_by_id[sid]).model_dump(), score=score)
        for sid, score in scored
        if sid in items_by_id
    ]

    return ResolveResult(
        method=m.method.value if m.method else None,
        confidence=m.confidence,
        weak=m.weak,
        candidates=candidates,
    )

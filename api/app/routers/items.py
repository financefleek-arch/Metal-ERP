"""Item catalogue CRUD, scoped to the caller's tenant.

Resolution (search / dedupe) reuses `domain.normalize` + the shared
`item_resolution` ladder. Everything created here starts UNCONFIRMED — a
hand-made item still passes through the review queue once.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import Integer, case, func, select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Item, ItemAlias, ItemCategory, ProductGroup
from app.models._mixins import ItemSource, ItemStatus, ItemType
from app.schemas_item import (
    BulkOutcome,
    ItemBulkDelete,
    ItemBulkDeleteResult,
    ItemBulkUpdate,
    ItemBulkUpdateResult,
    ItemCreate,
    ItemListItem,
    ItemMergeIn,
    ItemOut,
    ItemUpdate,
)
from app.services.catalogue.learn_from_recategorize import learn_from_recategorize
from app.services.items import (
    SEARCH_RESULT_CAP,
    apply_search,
    document_count,
    hsn_gst_rate,
    rate_in_band,
)
from app.services.pagination import finish_page, paginate

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


def _same(current: object, incoming: object) -> bool:
    """Would setting `incoming` be a no-op? Compares Decimals / enums by value
    so "5" vs Decimal('5.00') and ItemType.mrp vs "mrp" both count as equal.
    """
    if current is None or incoming is None:
        return current is None and incoming is None
    from decimal import Decimal, InvalidOperation

    for a, b in ((current, incoming), (incoming, current)):
        if isinstance(a, Decimal):
            try:
                return a == Decimal(str(b))
            except (InvalidOperation, TypeError):
                return False
    return str(getattr(current, "value", current)) == str(getattr(incoming, "value", incoming))


@router.get("", response_model=list[ItemListItem])
def list_items(
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    q: str | None = Query(default=None, description="fuzzy name / grade / size / HSN"),
    type_: ItemType | None = Query(default=None, alias="type"),
    status_: ItemStatus | None = Query(default=None, alias="status"),
    no_hsn: bool = Query(default=False),
    price_review: bool = Query(default=False),
    limit: int | None = Query(
        default=None, ge=1, description="page size; omit for the whole list"
    ),
    cursor: str | None = Query(default=None, description="opaque next-page token"),
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
        # Search is ranked by a non-deterministic fuzzy score, so keyset
        # paging doesn't apply — hard-cap the result instead. Narrow with a
        # second word, not another page.
        stmt = apply_search(stmt, session, q, tenant_id=user.tenant_id).limit(
            SEARCH_RESULT_CAP
        )
        return [_list_item(i) for i in session.scalars(stmt).unique().all()]

    # Default browse order: confirmed rows first, then name, then id (the
    # id is the stable tiebreak keyset paging needs). The "confirmed first"
    # key is an int (1/0), not a bool — keyset comparison needs `<`/`>`.
    confirmed_rank = case((Item.status == ItemStatus.confirmed, 1), else_=0).cast(Integer)
    stmt, paginated = paginate(
        stmt,
        order_cols=[confirmed_rank, func.lower(Item.name), Item.id],
        directions=["desc", "asc", "asc"],
        limit=limit,
        cursor=cursor,
    )
    rows = list(session.scalars(stmt).unique().all())
    if paginated:
        rows = finish_page(
            rows,
            limit=limit,
            key_of=lambda it: [
                1 if it.status == ItemStatus.confirmed else 0,
                it.name.lower(),
                it.id,
            ],
            response=response,
        )
    return [_list_item(i) for i in rows]


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
    leaf_count: int


class TreeCategory(BaseModel):
    id: str | None
    name: str
    groups: list[TreeGroup]
    loose_count: int  # leaves in this category with no group


def _active_item(tenant_id: str):  # type: ignore[no-untyped-def]
    return (
        Item.tenant_id == tenant_id,
        Item.merged_into_id.is_(None),
        Item.status != ItemStatus.archived,
    )


@router.get("/tree", response_model=list[TreeCategory])
def item_tree(user: CurrentUser, session: SessionDep) -> list[TreeCategory]:
    """The catalogue skeleton — categories → groups with leaf COUNTS only.

    Leaves are fetched per-node on expand via `/items/tree/leaves`, so this
    stays cheap (three aggregate queries) even at 10k items.
    """
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

    # count(active leaves) grouped by group_id
    leaf_counts: dict[str, int] = {
        gid: n
        for gid, n in session.execute(
            select(Item.group_id, func.count())
            .where(*_active_item(user.tenant_id), Item.group_id.is_not(None))
            .group_by(Item.group_id)
        ).all()
        if gid is not None
    }
    # count(active loose leaves) grouped by category_id (None = uncategorised)
    loose_counts: dict[str | None, int] = {
        cid: n
        for cid, n in session.execute(
            select(Item.category_id, func.count())
            .where(*_active_item(user.tenant_id), Item.group_id.is_(None))
            .group_by(Item.category_id)
        ).all()
    }

    groups_by_cat: dict[str | None, list[ProductGroup]] = {}
    for g in groups:
        groups_by_cat.setdefault(g.category_id, []).append(g)

    def tree_groups(cat_id: str | None) -> list[TreeGroup]:
        return [
            TreeGroup(
                id=g.id,
                name=g.name,
                item_type=g.item_type,
                leaf_count=leaf_counts.get(g.id, 0),
            )
            for g in groups_by_cat.get(cat_id, [])
        ]

    out: list[TreeCategory] = [
        TreeCategory(
            id=c.id,
            name=c.name,
            groups=tree_groups(c.id),
            loose_count=loose_counts.get(c.id, 0),
        )
        for c in cats
    ]
    unc_groups = groups_by_cat.get(None, [])
    unc_loose = loose_counts.get(None, 0)
    if unc_groups or unc_loose:
        out.append(
            TreeCategory(
                id=None, name="Uncategorised", groups=tree_groups(None), loose_count=unc_loose
            )
        )
    return out


@router.get("/tree/leaves", response_model=list[TreeLeaf])
def item_tree_leaves(
    user: CurrentUser,
    session: SessionDep,
    group_id: str | None = Query(default=None),
    category_id: str | None = Query(default=None, description="loose leaves in this category"),
    uncategorised: bool = Query(default=False, description="loose leaves with no category"),
) -> list[TreeLeaf]:
    """Leaves for one tree node — a group, or the loose bucket of a category.

    Exactly one selector: `group_id`, `category_id` (loose), or
    `uncategorised=true` (loose + no category).
    """
    selectors = [group_id is not None, category_id is not None, uncategorised]
    if sum(selectors) != 1:
        raise HTTPException(
            status_code=422,
            detail="pass exactly one of group_id / category_id / uncategorised",
        )

    stmt = select(Item).where(*_active_item(user.tenant_id))
    if group_id is not None:
        stmt = stmt.where(Item.group_id == group_id)
    elif category_id is not None:
        stmt = stmt.where(Item.group_id.is_(None), Item.category_id == category_id)
    else:
        stmt = stmt.where(Item.group_id.is_(None), Item.category_id.is_(None))

    # group leaves sort by size position; loose buckets by name
    stmt = stmt.order_by(
        func.coalesce(Item.size_pos, 9999) if group_id is not None else func.lower(Item.name),
        func.lower(Item.name),
    )
    rows = session.scalars(stmt).unique().all()
    return [
        TreeLeaf(
            id=it.id,
            name=it.name,
            size_label=it.size_label or it.size_text,
            default_rate=str(it.default_rate) if it.default_rate is not None else None,
            status=it.status,
        )
        for it in rows
    ]


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
    # Suggest category + group when the caller left both unset — a hand-made
    # item still lands unconfirmed (manual add always gets a review pass).
    if it.group_id is None and it.category_id is None:
        from app.services.catalogue.classify_apply import classify_one

        applied = classify_one(
            session, user.tenant_id, it.name, hsn=it.hsn_code, uom=it.uom
        )
        it.group_id = applied.group_id
        it.category_id = applied.category_id
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


# --------------------------------------------------------------------------
# bulk operations — declared BEFORE /{item_id} so "bulk" isn't read as an id
# --------------------------------------------------------------------------


def _bulk_items(
    session: SessionDep, tenant_id: str, ids: list[str]
) -> tuple[list[Item], list[str]]:
    """Load the given ids for this tenant, preserving request order. Returns
    (found_items, missing_ids). Merged / archived rows still load — the caller
    decides what to do with them.
    """
    seen: dict[str, Item] = {
        it.id: it
        for it in session.scalars(
            select(Item).where(Item.id.in_(ids), Item.tenant_id == tenant_id)
        ).all()
    }
    found = [seen[i] for i in ids if i in seen]
    missing = [i for i in ids if i not in seen]
    return found, missing


@router.patch("/bulk", response_model=ItemBulkUpdateResult)
def bulk_update(
    body: ItemBulkUpdate,
    user: WriteUser,
    session: SessionDep,
    dry_run: bool = Query(default=False),
) -> ItemBulkUpdateResult:
    """Apply one or a few fields to many items in a single transaction.

    Each item runs the *same* single-item logic (`_apply_group_inheritance`,
    HSN→GST fill, `learn_from_recategorize`). An item whose value already
    equals the target is reported `skipped`, not rewritten. A per-item failure
    (name clash, unknown group) is reported `error` and rolled back for that
    row only; the rest proceed. `dry_run=true` computes the same outcome rows
    without persisting.
    """
    patch = body.fields.model_dump(exclude_unset=True)
    chosen = set(body.fields_set) if body.fields_set else set(patch.keys())
    patch = {k: v for k, v in patch.items() if k in chosen}
    if not patch:
        raise HTTPException(status_code=422, detail="no fields to change")

    found, missing = _bulk_items(session, user.tenant_id, body.ids)
    rows: list[BulkOutcome] = [
        BulkOutcome(id=mid, name="—", result="error", detail="not found") for mid in missing
    ]
    learned: list[str] = []
    changed = unchanged = 0

    # name is not bulk-editable, so a normalized-key check is unnecessary here.
    for it in found:
        if it.merged_into_id is not None:
            rows.append(BulkOutcome(id=it.id, name=it.name, result="skipped", detail="merged away"))
            unchanged += 1
            continue

        diff: dict[str, object] = {}
        skip_reason: str | None = None
        for field_, value in patch.items():
            # MRP-only field on a BULK item (unless this same patch flips it to mrp)
            if field_ == "default_discount_pct":
                target_type = patch.get("item_type", it.item_type)
                if str(target_type) != str(ItemType.mrp) and target_type != ItemType.mrp:
                    skip_reason = "BULK item — discount % not applicable"
                    continue
            current = getattr(it, field_)
            if _same(current, value):
                continue
            diff[field_] = value

        if skip_reason and not diff:
            rows.append(BulkOutcome(id=it.id, name=it.name, result="skipped", detail=skip_reason))
            unchanged += 1
            continue
        if not diff:
            rows.append(
                BulkOutcome(id=it.id, name=it.name, result="skipped", detail="already up to date")
            )
            unchanged += 1
            continue

        detail_bits = [f"{k} → {diff[k]}" for k in diff]
        if skip_reason:
            detail_bits.append(skip_reason)

        if dry_run:
            rows.append(
                BulkOutcome(id=it.id, name=it.name, result="changed", detail="; ".join(detail_bits))
            )
            changed += 1
            continue

        sp = session.begin_nested()
        try:
            group_changed = "group_id" in diff and diff["group_id"] != it.group_id
            hsn_changed = "hsn_code" in diff and diff["hsn_code"] != it.hsn_code
            was_unconfirmed = it.status == ItemStatus.unconfirmed
            if "notes" in diff and body.notes_mode == "append" and it.notes:
                diff["notes"] = f"{it.notes}\n{diff['notes']}"
            for field_, value in diff.items():
                setattr(it, field_, value)
            if group_changed:
                _apply_group_inheritance(session, user.tenant_id, it, set(diff.keys()))
                if it.group_id:
                    rule = learn_from_recategorize(
                        session, user.tenant_id, it, it.group_id,
                        was_unconfirmed=was_unconfirmed,
                    )
                    if rule is not None:
                        learned.append(rule.id)
            if hsn_changed:
                rate = hsn_gst_rate(session, it.hsn_code)
                if rate is not None:
                    it.gst_rate = float(rate)
            session.flush()
            sp.commit()
        except Exception as exc:  # noqa: BLE001 — report, don't abort the batch
            sp.rollback()
            rows.append(
                BulkOutcome(id=it.id, name=it.name, result="error", detail=str(exc)[:200])
            )
            continue
        rows.append(
            BulkOutcome(id=it.id, name=it.name, result="changed", detail="; ".join(detail_bits))
        )
        changed += 1

    if dry_run:
        session.rollback()

    order = {i: n for n, i in enumerate(body.ids)}
    rows.sort(key=lambda r: order.get(r.id, 1_000_000))
    return ItemBulkUpdateResult(
        dry_run=dry_run,
        changed=changed,
        unchanged=unchanged,
        errors=sum(1 for r in rows if r.result == "error"),
        learned_rule_ids=sorted(set(learned)),
        rows=rows,
    )


@router.post("/bulk-delete", response_model=ItemBulkDeleteResult)
def bulk_delete(
    body: ItemBulkDelete,
    user: WriteUser,
    session: SessionDep,
    dry_run: bool = Query(default=False),
) -> ItemBulkDeleteResult:
    """Delete many items. An item on any document keeps the single-item 409
    rule: it is `blocked`, or `archived` when `on_blocked=archive`. `dry_run`
    reports the deletable / blocked split without touching anything.
    """
    found, missing = _bulk_items(session, user.tenant_id, body.ids)
    rows: list[BulkOutcome] = [
        BulkOutcome(id=mid, name="—", result="error", detail="not found") for mid in missing
    ]
    deleted = archived = blocked = 0

    for it in found:
        if it.merged_into_id is not None:
            rows.append(
                BulkOutcome(id=it.id, name=it.name, result="error", detail="already merged")
            )
            continue
        refs = document_count(session, it.id)
        n = max(refs, it.times_billed)
        if n > 0:
            if body.on_blocked == "archive":
                if not dry_run:
                    it.status = ItemStatus.archived
                rows.append(
                    BulkOutcome(
                        id=it.id, name=it.name, result="archived",
                        detail=f"on {n} document{'s' if n != 1 else ''}",
                    )
                )
                archived += 1
            else:
                rows.append(
                    BulkOutcome(
                        id=it.id, name=it.name, result="blocked",
                        detail=f"on {n} document{'s' if n != 1 else ''} — archive instead",
                    )
                )
                blocked += 1
            continue
        if not dry_run:
            session.delete(it)
        rows.append(BulkOutcome(id=it.id, name=it.name, result="deleted", detail="never billed"))
        deleted += 1

    if not dry_run:
        session.flush()

    order = {i: n for n, i in enumerate(body.ids)}
    rows.sort(key=lambda r: order.get(r.id, 1_000_000))
    return ItemBulkDeleteResult(
        dry_run=dry_run,
        deleted=deleted,
        archived=archived,
        blocked=blocked,
        errors=sum(1 for r in rows if r.result == "error"),
        rows=rows,
    )


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
    was_unconfirmed = it.status == ItemStatus.unconfirmed
    for field, value in patch.items():
        setattr(it, field, value)
    if group_changed:
        _apply_group_inheritance(session, user.tenant_id, it, set(patch.keys()))
        # A recategorise on an unconfirmed item teaches a tenant classify rule.
        if it.group_id:
            learn_from_recategorize(
                session, user.tenant_id, it, it.group_id,
                was_unconfirmed=was_unconfirmed,
            )
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

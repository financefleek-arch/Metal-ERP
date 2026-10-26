"""Sort groups that have no category into categories.

A group's category is suggested by classifying its items by name (the same classifier an item
gets on creation) and taking the most common answer; the share of items that agree is the
confidence. The group's own name counts as a few extra votes, so a short group of oddly named
items still lands where its name points. Nothing is moved here: `suggest` only reads, `assign`
is the one write and carries each group's items with it, as editing a group's category does.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.item_classify import classify_item
from app.domain.normalize import load_synonym_map
from app.models import Item, ItemCategory, ProductGroup
from app.models._mixins import ItemStatus
from app.services.catalogue.classify_apply import _category_id_for, load_learned_rules
from app.services.catalogue.common import get_or_create_category

SAMPLE_PER_GROUP = 300
# Below this share of agreeing items there is no suggestion, only the breakdown.
SUGGEST_FLOOR = 0.4
_CHUNK = 500
_MAX_NEW_NAME_WORDS = 2


@dataclass
class GroupSuggestion:
    group_id: str
    name: str
    item_count: int
    category_id: str | None = None
    category_name: str | None = None
    confidence: float = 0.0
    breakdown: list[tuple[str, float]] = field(default_factory=list)
    new_category_name: str | None = None  # a short group name that looks like a brand


@dataclass
class Move:
    group_id: str
    category_id: str | None = None  # None with no new name = take it out of its category
    new_category_name: str | None = None


@dataclass
class AssignResult:
    groups: int = 0
    items: int = 0
    created_categories: list[str] = field(default_factory=list)


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split())[:60]


def suggest(session: Session, tenant_id: str) -> list[GroupSuggestion]:
    groups = session.execute(
        select(ProductGroup.id, ProductGroup.name).where(
            ProductGroup.tenant_id == tenant_id, ProductGroup.category_id.is_(None)
        )
    ).all()
    if not groups:
        return []

    syn = load_synonym_map(session, tenant_id)
    learned = load_learned_rules(session, tenant_id)
    cat_names = dict(
        session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    )
    cat_cache: dict[str, str | None] = {}

    ids = [g.id for g in groups]
    counts: Counter[str] = Counter()
    samples: dict[str, list[tuple[str, str | None, str | None]]] = {g.id: [] for g in groups}
    for i in range(0, len(ids), _CHUNK):
        rows = session.execute(
            select(Item.group_id, Item.name, Item.hsn_code, Item.uom).where(
                Item.tenant_id == tenant_id,
                Item.group_id.in_(ids[i : i + _CHUNK]),
                Item.merged_into_id.is_(None),
                Item.status != ItemStatus.archived.value,
            )
        ).all()
        for gid, name, hsn, uom in rows:
            counts[gid] += 1
            if len(samples[gid]) < SAMPLE_PER_GROUP:
                samples[gid].append((name, hsn, uom))

    def vote(name: str, hsn: str | None, uom: str | None) -> str | None:
        res = classify_item(name, hsn=hsn, uom=uom, synonyms=syn, learned=learned)
        if res.is_other or not res.assignable:
            return None
        return _category_id_for(session, tenant_id, res.department, res.brand, cat_cache)

    out: list[GroupSuggestion] = []
    for g in groups:
        sample = samples[g.id]
        votes: Counter[str] = Counter()
        total = len(sample)
        for name, hsn, uom in sample:
            cid = vote(name, hsn, uom)
            if cid:
                votes[cid] += 1
        by_name = vote(g.name, None, None)
        if by_name:
            extra = max(3, total // 4)
            votes[by_name] += extra
            total += extra

        s = GroupSuggestion(group_id=g.id, name=g.name, item_count=counts[g.id])
        if votes and total:
            s.breakdown = [
                (cat_names.get(cid, "?"), round(n / total, 2)) for cid, n in votes.most_common(3)
            ]
            top, n = votes.most_common(1)[0]
            share = n / total
            if share >= SUGGEST_FLOOR:
                s.category_id = top
                s.category_name = cat_names.get(top)
                s.confidence = round(share, 2)
        if s.category_id is None and 0 < len(g.name.split()) <= _MAX_NEW_NAME_WORDS:
            s.new_category_name = _title(g.name)
        out.append(s)

    out.sort(key=lambda s: (-s.item_count, s.name.lower()))
    return out


def assign(session: Session, tenant_id: str, moves: list[Move]) -> AssignResult:
    """Put each group under a category and carry its items along. A move with neither a category
    nor a new name takes the group out of its category (the undo of an earlier move).
    """
    res = AssignResult()
    seen: set[str] = set()
    for m in moves:
        if m.group_id in seen:
            continue
        seen.add(m.group_id)
        g = session.scalar(
            select(ProductGroup).where(
                ProductGroup.id == m.group_id, ProductGroup.tenant_id == tenant_id
            )
        )
        if g is None:
            raise LookupError(f"Group {m.group_id} not found")

        target: str | None
        if m.new_category_name and m.new_category_name.strip():
            cat, created = get_or_create_category(session, tenant_id, m.new_category_name)
            if created:
                res.created_categories.append(cat.name)
            target = cat.id
        elif m.category_id:
            cat = session.scalar(
                select(ItemCategory).where(
                    ItemCategory.id == m.category_id, ItemCategory.tenant_id == tenant_id
                )
            )
            if cat is None:
                raise LookupError(f"Category {m.category_id} not found")
            target = cat.id
        else:
            target = None

        g.category_id = target
        moved = session.execute(
            update(Item)
            .where(Item.tenant_id == tenant_id, Item.group_id == g.id)
            .values(category_id=target)
        ).rowcount
        res.groups += 1
        res.items += moved or 0
    session.flush()
    return res


def count_uncategorised(session: Session, tenant_id: str) -> tuple[int, int]:
    """(groups with no category, items in them)."""
    groups = (
        session.scalar(
            select(func.count())
            .select_from(ProductGroup)
            .where(ProductGroup.tenant_id == tenant_id, ProductGroup.category_id.is_(None))
        )
        or 0
    )
    items = (
        session.scalar(
            select(func.count())
            .select_from(Item)
            .join(ProductGroup, ProductGroup.id == Item.group_id)
            .where(
                Item.tenant_id == tenant_id,
                ProductGroup.category_id.is_(None),
                Item.merged_into_id.is_(None),
                Item.status != ItemStatus.archived.value,
            )
        )
        or 0
    )
    return groups, items

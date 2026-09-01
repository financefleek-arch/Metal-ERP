"""Resolve a free-text line description to a catalogue `item`.

The ladder, stop at the first hit:
  1. exact   normalize(description) == item.name_normalized      conf 1.00
  2. alias   normalize(description) == item_alias.alias_normalized conf 0.98
  3. fuzzy   pg_trgm similarity >= 0.55 over name + aliases, HSN as
             boost (+0.15 same) / penalty (-0.10 different); take the top
             only if adjusted >= 0.72 AND it beats the runner-up by >= 0.10
  (4. LLM disambiguation — layered on by the caller when weak/ambiguous; X3)
  (5. stage new — the caller builds `new_item_staged_json` when method is None)

Shared by M1 sales-finalize accretion and the inward line-matcher.

Postgres: step 3 uses `similarity()`. SQLite (tests): step 3 is skipped —
`method` comes back None and the caller stages a new item. The Sugal Foods
fixture exercises exactly that path (empty catalogue → every line NEW).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Item, ItemAlias, ProductGroup
from app.models._mixins import ItemStatus, MatchMethod

_FUZZY_FLOOR = 0.55
_FUZZY_ACCEPT = 0.72
_RUNNER_UP_GAP = 0.10
_HSN_BOOST = 0.15
_HSN_PENALTY = 0.10


@dataclass
class Candidate:
    item_id: str
    name: str
    hsn: str | None
    raw_score: float
    adjusted_score: float


@dataclass
class ItemMatch:
    item_id: str | None
    method: MatchMethod | None
    confidence: float | None
    candidates: list[Candidate] = field(default_factory=list)
    # True when step 3 ran but produced nothing confident enough — the caller
    # may consult the LLM (X3) before falling through to stage-new.
    weak: bool = False


def _is_postgres(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def resolve_item(
    session: Session,
    tenant_id: str,
    description: str,
    hsn: str | None = None,
    *,
    synonyms: dict[str, str] | None = None,
) -> ItemMatch:
    if synonyms is None:
        synonyms = load_synonym_map(session, tenant_id)
    key = normalize_name(description, synonyms)
    if not key:
        return ItemMatch(item_id=None, method=None, confidence=None)

    # --- step 1: exact on item.name_normalized ---
    exact = session.scalar(
        select(Item).where(
            Item.tenant_id == tenant_id,
            Item.name_normalized == key,
            Item.status != ItemStatus.archived,
            Item.merged_into_id.is_(None),
        )
    )
    if exact is not None:
        return ItemMatch(exact.id, MatchMethod.exact, 1.0)

    # --- step 2: alias ---
    alias = session.scalar(
        select(ItemAlias).where(
            ItemAlias.tenant_id == tenant_id,
            ItemAlias.alias_normalized == key,
        )
    )
    if alias is not None:
        return ItemMatch(alias.item_id, MatchMethod.alias, 0.98)

    # --- step 3: trigram fuzzy (Postgres only) ---
    if not _is_postgres(session):
        return ItemMatch(item_id=None, method=None, confidence=None, weak=False)

    sim = func.similarity(Item.name_normalized, key)
    rows = session.execute(
        select(Item.id, Item.name, Item.hsn_code, sim.label("s"))
        .where(
            Item.tenant_id == tenant_id,
            Item.status != ItemStatus.archived,
            Item.merged_into_id.is_(None),
            sim >= _FUZZY_FLOOR,
        )
        .order_by(sim.desc())
        .limit(8)
    ).all()

    if not rows:
        return ItemMatch(item_id=None, method=None, confidence=None, weak=False)

    cands: list[Candidate] = []
    for item_id, name, item_hsn, s in rows:
        adj = float(s)
        if hsn and item_hsn:
            adj += _HSN_BOOST if item_hsn == hsn else -_HSN_PENALTY
        cands.append(Candidate(item_id, name, item_hsn, float(s), adj))
    cands.sort(key=lambda c: c.adjusted_score, reverse=True)

    top = cands[0]
    runner_up = cands[1].adjusted_score if len(cands) > 1 else 0.0
    if top.adjusted_score >= _FUZZY_ACCEPT and (top.adjusted_score - runner_up) >= _RUNNER_UP_GAP:
        return ItemMatch(
            top.item_id,
            MatchMethod.fuzzy,
            round(min(top.adjusted_score, 0.99), 3),
            candidates=cands,
        )

    # Weak / ambiguous — caller decides (LLM in X3, else stage-new).
    return ItemMatch(item_id=None, method=None, confidence=None, candidates=cands, weak=True)


# --------------------------------------------------------------------------
# group resolution — the middle level of category → group → item
# --------------------------------------------------------------------------


@dataclass
class GroupMatch:
    group_id: str | None
    method: MatchMethod | None  # exact | alias | fuzzy | None
    confidence: float | None
    name: str | None = None


def resolve_group(
    session: Session,
    tenant_id: str,
    text: str,
    *,
    synonyms: dict[str, str] | None = None,
) -> GroupMatch:
    """Resolve free text to a `product_group`: exact name_normalized ->
    group-scoped alias -> trigram (Postgres only).
    """
    if synonyms is None:
        synonyms = load_synonym_map(session, tenant_id)
    key = normalize_name(text, synonyms)
    if not key:
        return GroupMatch(None, None, None)

    exact = session.scalar(
        select(ProductGroup).where(
            ProductGroup.tenant_id == tenant_id,
            ProductGroup.name_normalized == key,
        )
    )
    if exact is not None:
        return GroupMatch(exact.id, MatchMethod.exact, 1.0, exact.name)

    alias = session.scalar(
        select(ItemAlias).where(
            ItemAlias.tenant_id == tenant_id,
            ItemAlias.alias_normalized == key,
            ItemAlias.group_id.is_not(None),
        )
    )
    if alias is not None:
        grp = session.get(ProductGroup, alias.group_id)
        return GroupMatch(
            alias.group_id, MatchMethod.alias, 0.98, grp.name if grp else None
        )

    if not _is_postgres(session):
        return GroupMatch(None, None, None)

    sim = func.similarity(ProductGroup.name_normalized, key)
    row = session.execute(
        select(ProductGroup.id, ProductGroup.name, sim.label("s"))
        .where(
            ProductGroup.tenant_id == tenant_id,
            sim >= _FUZZY_FLOOR,
        )
        .order_by(sim.desc())
        .limit(2)
    ).all()
    if len(row) == 1 and float(row[0].s) >= _FUZZY_ACCEPT:
        return GroupMatch(
            row[0].id, MatchMethod.fuzzy, round(float(row[0].s), 3), row[0].name
        )
    return GroupMatch(None, None, None)

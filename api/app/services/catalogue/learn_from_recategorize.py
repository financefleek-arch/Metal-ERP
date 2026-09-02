"""Loop: a user recategorises an item -> a tenant classify rule.

When someone moves an `unconfirmed` item into a different group in the Items
screen, the distinctive phrase of that item's normalised name is stored as
an `item_classify_rule` pointing at the chosen group. The next import files
matching items automatically. Mirrors the learned-alias loop.

"Distinctive phrase" = the item's normalised name with size tokens and pure
numbers dropped, capped to a few words. Deliberately conservative: a rule
that is too specific just never fires again; one that is too broad
mis-files. We only write when the item is (or was) unconfirmed — a
confirmed item being moved is a correction to one row, not a teachable
pattern.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Item, ItemCategory, ItemClassifyRule, ProductGroup

# tokens that carry no product-type meaning — drop before forming the phrase
_STOP = {
    "ss", "s", "gi", "g", "al", "alu", "new", "old", "set", "pcs", "pc",
    "no", "nos", "with", "and", "the", "for", "size", "of", "big", "small",
    "large", "medium", "jumbo", "heavy", "light", "hvy",
}
_NUM_OR_SIZE = re.compile(r"^\d+(\.\d+)?([a-z]{1,3})?$")   # 240, 3.5l, 12x18 handled below
_DIM = re.compile(r"^\d+(\.\d+)?x\d+(\.\d+)?$")
_MAX_WORDS = 4


def _phrase_from_name(name: str, synonyms: dict[str, str]) -> str:
    norm = normalize_name(name, synonyms)
    words = [
        w for w in norm.split()
        if w not in _STOP and not _NUM_OR_SIZE.match(w) and not _DIM.match(w)
    ]
    return " ".join(words[:_MAX_WORDS]).strip()


def learn_from_recategorize(
    session: Session,
    tenant_id: str,
    item: Item,
    new_group_id: str,
    *,
    was_unconfirmed: bool,
) -> ItemClassifyRule | None:
    """Write / refresh a learned rule from this recategorise. Returns the rule
    row, or None when nothing teachable. Caller commits.
    """
    if not was_unconfirmed:
        return None
    grp = session.get(ProductGroup, new_group_id)
    if grp is None or grp.tenant_id != tenant_id:
        return None
    department = (
        session.scalar(
            select(ItemCategory.name).where(ItemCategory.id == grp.category_id)
        )
        if grp.category_id
        else None
    ) or ""

    synonyms = load_synonym_map(session, tenant_id)
    phrase = _phrase_from_name(item.name, synonyms)
    if not phrase or len(phrase) < 3:
        return None

    now = datetime.now(UTC)
    existing = session.scalar(
        select(ItemClassifyRule).where(
            ItemClassifyRule.tenant_id == tenant_id,
            ItemClassifyRule.phrase_normalized == phrase,
        )
    )
    if existing is not None:
        existing.group_id = new_group_id
        existing.department = department or existing.department
        existing.hits += 1
        existing.last_used_at = now
        return existing

    rule = ItemClassifyRule(
        tenant_id=tenant_id,
        phrase_normalized=phrase,
        department=department,
        group_id=new_group_id,
        source="learned",
        hits=1,
        last_used_at=now,
    )
    session.add(rule)
    session.flush()
    return rule

"""Bridge the pure classifier to the DB: resolve a name (+HSN/UOM) to a
real `category_id`, `group_id` and `status` for this tenant.

Used by every item-creation path (Tally import fallback, inward, billing
type-ahead, manual) and by `tools/reclassify_items.py` (the one-time
backfill). The taxonomy groups are assumed to already exist for the tenant
(seed_taxonomy runs on register / as a top-up) — a missing group is created
on the fly so a create never fails.

The hybrid category rule:
  * a brand recognised AND that brand exists as an item_category  -> category = brand
  * otherwise                                                     -> category = department
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.item_classify import (
    CONFIDENCE_CONFIRM,
    ClassifyResult,
    LearnedRule,
    classify_item,
)
from app.domain.normalize import load_synonym_map, normalize_name
from app.models import ItemCategory, ItemClassifyRule, ProductGroup
from app.models._mixins import ItemStatus, ItemType, RateMode


@dataclass
class AppliedClassification:
    category_id: str | None
    group_id: str | None
    status: ItemStatus
    result: ClassifyResult   # the raw classifier output, for logging / CSV


def load_learned_rules(session: Session, tenant_id: str) -> list[LearnedRule]:
    rows = session.execute(
        select(
            ItemClassifyRule.phrase_normalized,
            ItemClassifyRule.department,
            ProductGroup.name,
        )
        .join(ProductGroup, ProductGroup.id == ItemClassifyRule.group_id, isouter=True)
        .where(ItemClassifyRule.tenant_id == tenant_id)
    ).all()
    return [
        LearnedRule(phrase=ph, department=dept, group=grp or "")
        for ph, dept, grp in rows
        if grp
    ]


def _category_id_for(
    session: Session, tenant_id: str, dept: str, brand: str | None,
    cat_cache: dict[str, str | None],
) -> str | None:
    """brand category if the brand exists as one, else the department category."""
    for candidate in ((brand,) if brand else ()) + (dept,):
        if candidate is None:
            continue
        low = candidate.lower()
        if low in cat_cache:
            if cat_cache[low] is not None:
                return cat_cache[low]
            continue
        cid = session.scalar(
            select(ItemCategory.id).where(
                ItemCategory.tenant_id == tenant_id,
                func.lower(ItemCategory.name) == low,
            )
        )
        cat_cache[low] = cid
        if cid is not None:
            return cid
    return None


def _group_id_for(
    session: Session, tenant_id: str, dept: str, grp_name: str,
    synonyms: dict[str, str], grp_cache: dict[str, str],
    cat_cache: dict[str, str | None],
) -> str | None:
    key = normalize_name(grp_name, synonyms)
    if not key:
        return None
    if key in grp_cache:
        return grp_cache[key]
    gid = session.scalar(
        select(ProductGroup.id).where(
            ProductGroup.tenant_id == tenant_id,
            ProductGroup.name_normalized == key,
        )
    )
    if gid is None:
        # taxonomy group not seeded yet (e.g. tenant predates a taxonomy
        # revision) — create it so the item still gets filed.
        cat_id = _category_id_for(session, tenant_id, dept, None, cat_cache)
        grp = ProductGroup(
            tenant_id=tenant_id,
            name=grp_name,
            name_normalized=key,
            category_id=cat_id,
            item_type=ItemType.mrp,
            default_rate_mode=RateMode.piece,
        )
        session.add(grp)
        session.flush()
        gid = grp.id
    grp_cache[key] = gid
    return gid


class Classifier:
    """Stateful per-tenant helper — loads synonyms + learned rules once, then
    `apply()` per item. Cheap to construct; reuse it across a batch.
    """

    def __init__(self, session: Session, tenant_id: str):
        self.session = session
        self.tenant_id = tenant_id
        self.synonyms = load_synonym_map(session, tenant_id)
        self.learned = load_learned_rules(session, tenant_id)
        self._cat_cache: dict[str, str | None] = {}
        self._grp_cache: dict[str, str] = {}

    def apply(
        self,
        name: str,
        *,
        hsn: str | None = None,
        uom: str | None = None,
        force_unconfirmed: bool = False,
    ) -> AppliedClassification:
        res = classify_item(
            name, hsn=hsn, uom=uom,
            synonyms=self.synonyms, learned=self.learned,
        )
        cat_id = _category_id_for(
            self.session, self.tenant_id, res.department, res.brand, self._cat_cache
        )
        grp_id = _group_id_for(
            self.session, self.tenant_id, res.department, res.group,
            self.synonyms, self._grp_cache, self._cat_cache,
        )
        if force_unconfirmed or res.confidence < CONFIDENCE_CONFIRM:
            status = ItemStatus.unconfirmed
        else:
            status = ItemStatus.confirmed
        return AppliedClassification(cat_id, grp_id, status, res)


def classify_one(
    session: Session, tenant_id: str, name: str, *,
    hsn: str | None = None, uom: str | None = None,
) -> AppliedClassification:
    """One-shot convenience for a single create (inward line, type-ahead)."""
    return Classifier(session, tenant_id).apply(name, hsn=hsn, uom=uom)

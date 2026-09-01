"""Shared catalogue-learning primitives.

Used by Loop 2 (`learn_from_invoice`, this slice) and, when it lands, Loop
1 (`learn_from_inward`): resolve-or-create a product group + its category,
and write an alias without tripping the unique key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.normalize import normalize_name
from app.domain.product_parse import ParsedLine
from app.models import ItemAlias, ItemCategory, ProductGroup
from app.models._mixins import AliasSource, ItemType, RateMode
from app.services.item_resolution import resolve_group


@dataclass
class GroupResolution:
    group: ProductGroup
    created: bool
    category_created: bool


def get_or_create_category(
    session: Session, tenant_id: str, name: str
) -> tuple[ItemCategory, bool]:
    name = name.strip()
    existing = session.scalar(
        select(ItemCategory).where(
            ItemCategory.tenant_id == tenant_id,
            func.lower(ItemCategory.name) == name.lower(),
        )
    )
    if existing is not None:
        return existing, False
    max_sort = (
        session.scalar(
            select(func.coalesce(func.max(ItemCategory.sort), 0)).where(
                ItemCategory.tenant_id == tenant_id
            )
        )
        or 0
    )
    cat = ItemCategory(tenant_id=tenant_id, name=name, sort=max_sort + 1)
    session.add(cat)
    session.flush()
    return cat, True


def resolve_or_create_group(
    session: Session,
    tenant_id: str,
    text: str,
    *,
    parsed: ParsedLine | None = None,
    hsn_code: str | None = None,
    uom: str | None = None,
    rate_mode: RateMode | None = None,
    synonyms: dict[str, str] | None = None,
) -> GroupResolution | None:
    """Find the product group `text` names, or create it.

    Returns None when `text` normalises to nothing (a group needs a key).
    On create: category from the parsed brand (created if new); HSN / UOM /
    rate_mode from the line.
    """
    key = normalize_name(text, synonyms or {})
    if not key:
        return None

    match = resolve_group(session, tenant_id, text, synonyms=synonyms)
    if match.group_id is not None:
        grp = session.get(ProductGroup, match.group_id)
        if grp is not None:
            return GroupResolution(group=grp, created=False, category_created=False)

    # --- create ---
    category_id: str | None = None
    category_created = False
    brand = (parsed.brand if parsed else None) or None
    if brand:
        cat, category_created = get_or_create_category(session, tenant_id, brand)
        category_id = cat.id

    group_name = _group_display_name(text, parsed)
    grp = ProductGroup(
        tenant_id=tenant_id,
        name=group_name,
        name_normalized=key,
        category_id=category_id,
        hsn_code=hsn_code,
        uom=uom,
        item_type=ItemType.bulk,
        default_rate_mode=rate_mode or RateMode.piece,
    )
    session.add(grp)
    session.flush()
    return GroupResolution(group=grp, created=True, category_created=category_created)


def _group_display_name(text: str, parsed: ParsedLine | None) -> str:
    if parsed and (parsed.brand or parsed.product):
        parts = [p for p in (parsed.brand, parsed.product) if p]
        name = " ".join(parts).strip()
        if name:
            return name[:200]
    return text.strip()[:200] or text.strip()


def write_alias(
    session: Session,
    tenant_id: str,
    *,
    alias_text: str,
    item_id: str | None = None,
    group_id: str | None = None,
    source: AliasSource = AliasSource.learned,
    synonyms: dict[str, str] | None = None,
    now: datetime | None = None,
) -> ItemAlias | None:
    """Idempotent alias write. Skips when the normalised key already exists
    (for any target) or normalises to nothing. Exactly one of item_id /
    group_id must be set.
    """
    if (item_id is None) == (group_id is None):
        raise ValueError("write_alias needs exactly one of item_id / group_id")
    key = normalize_name(alias_text, synonyms or {})
    if not key:
        return None
    existing = session.scalar(
        select(ItemAlias).where(
            ItemAlias.tenant_id == tenant_id,
            ItemAlias.alias_normalized == key,
        )
    )
    if existing is not None:
        # keep the freshest touch for the sweep clock
        if source == AliasSource.learned and now is not None:
            existing.last_used_at = now
        return existing
    alias = ItemAlias(
        tenant_id=tenant_id,
        item_id=item_id,
        group_id=group_id,
        alias_text=alias_text.strip()[:300],
        alias_normalized=key,
        source=source,
        last_used_at=now,
    )
    session.add(alias)
    session.flush()
    return alias

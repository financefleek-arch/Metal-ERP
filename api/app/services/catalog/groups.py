"""Our group for a catalog item = an `item_category`, found or created by name.

Case-insensitive on the name, so "beer mugs" reuses "Beer Mugs". Creation is
automatic when the tenant policy is `auto`, and always when the user names a
group themselves.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ItemCategory

_MAX_NAME = 60


def clean_group_name(name: str | None) -> str | None:
    """Collapse whitespace and cap the length; None when blank."""
    if name is None:
        return None
    cleaned = re.sub(r"\s+", " ", name).strip()[:_MAX_NAME].strip()
    return cleaned or None


def find_category(session: Session, tenant_id: str, name: str) -> ItemCategory | None:
    return session.scalar(
        select(ItemCategory).where(
            ItemCategory.tenant_id == tenant_id,
            func.lower(ItemCategory.name) == name.lower(),
        )
    )


def get_or_create_category(
    session: Session,
    tenant_id: str,
    name: str,
    cache: dict[str, ItemCategory] | None = None,
) -> ItemCategory:
    """The category called `name`, creating it when missing. `cache` (lower-case
    name -> row) saves a query per item during a bulk import.
    """
    key = name.lower()
    if cache is not None and key in cache:
        return cache[key]
    cat = find_category(session, tenant_id, name)
    if cat is None:
        top = session.scalar(
            select(func.max(ItemCategory.sort)).where(ItemCategory.tenant_id == tenant_id)
        )
        cat = ItemCategory(tenant_id=tenant_id, name=name, sort=(top or 0) + 1)
        try:
            with session.begin_nested():
                session.add(cat)
        except IntegrityError:  # a concurrent request created it first
            cat = find_category(session, tenant_id, name)
            assert cat is not None
    if cache is not None:
        cache[key] = cat
    return cat

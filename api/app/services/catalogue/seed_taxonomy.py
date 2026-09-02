"""Materialise the fixed item taxonomy for a tenant.

Idempotent. Creates:
  * one `item_category` per department  (item_taxonomy.DEPARTMENTS)
  * one `item_category` per starter brand  (STARTER_BRAND_CATEGORIES)
  * one `product_group` per (department, group) in item_taxonomy.all_group_names()

Called on register (replacing the old ad-hoc `_SEED_CATEGORIES` list) and as a
top-up for an existing tenant:

    python -m app.services.catalogue.seed_taxonomy --tenant <id>
    python -m app.services.catalogue.seed_taxonomy --all

Nothing is renamed or deleted — a shop that already customised a category
keeps it; this only adds what is missing.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.item_taxonomy import (
    DEPARTMENTS,
    STARTER_BRAND_CATEGORIES,
    all_group_names,
)
from app.domain.normalize import load_synonym_map, normalize_name
from app.models import ItemCategory, ProductGroup
from app.models._mixins import ItemType, RateMode


@dataclass
class SeedResult:
    categories_created: int
    groups_created: int


def seed_taxonomy(session: Session, tenant_id: str) -> SeedResult:
    """Add every missing department, starter brand and product group for the
    tenant. Caller commits.
    """
    synonyms = load_synonym_map(session, tenant_id)

    # --- categories: departments then brands ---
    existing_cats: dict[str, str] = {
        name.lower(): cid
        for name, cid in session.execute(
            select(ItemCategory.name, ItemCategory.id).where(
                ItemCategory.tenant_id == tenant_id
            )
        ).all()
    }
    cats_created = 0
    for sort, name in enumerate([*DEPARTMENTS, *STARTER_BRAND_CATEGORIES]):
        if name.lower() not in existing_cats:
            cat = ItemCategory(tenant_id=tenant_id, name=name, sort=sort)
            session.add(cat)
            session.flush()
            existing_cats[name.lower()] = cat.id
            cats_created += 1

    # --- groups: one per (department, group) ---
    existing_group_keys: set[str] = set(
        session.scalars(
            select(ProductGroup.name_normalized).where(
                ProductGroup.tenant_id == tenant_id
            )
        ).all()
    )
    groups_created = 0
    for dept, grp in all_group_names():
        key = normalize_name(grp, synonyms)
        if not key or key in existing_group_keys:
            continue
        session.add(
            ProductGroup(
                tenant_id=tenant_id,
                name=grp,
                name_normalized=key,
                category_id=existing_cats.get(dept.lower()),
                item_type=ItemType.mrp,
                default_rate_mode=RateMode.piece,
            )
        )
        existing_group_keys.add(key)
        groups_created += 1

    session.flush()
    return SeedResult(categories_created=cats_created, groups_created=groups_created)


def _main() -> None:
    ap = argparse.ArgumentParser(description="Seed the item taxonomy for a tenant")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="tenant id")
    g.add_argument("--all", action="store_true", help="every tenant")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.db import SessionLocal
    from app.models import Tenant

    with SessionLocal() as session:
        tenant_ids = (
            list(session.scalars(select(Tenant.id)).all())
            if args.all
            else [args.tenant]
        )
        for tid in tenant_ids:
            res = seed_taxonomy(session, tid)
            print(
                f"{tid}: +{res.categories_created} categories, "
                f"+{res.groups_created} groups"
            )
        if args.dry_run:
            session.rollback()
            print("(dry run — rolled back)")
        else:
            session.commit()


if __name__ == "__main__":
    _main()

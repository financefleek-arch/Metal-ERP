"""Loop 2 — the billing side of catalogue learning.

Runs inside the invoice-finalize transaction, after each line has been
resolved/created as an `item`. For every line it:

  1. parses the typed description (rules-first `product_parse`)
  2. if the line's item has no `group_id`, resolves-or-creates a product
     group (+ its category) and attaches the item to it — so linking an
     invoice to a product backfills the product's categorization
  3. writes the typed wording as an alias:
       - group alias  -> so "hawkins 5" hits the group next time
       - leaf alias   -> so the exact shorthand is an instant match
     `source=learned` for a resolved item, `auto_from_invoice` for a
     brand-new item created this finalize (never auto-retired for those)

Everything created here stays `status=unconfirmed` — the item router's
create path already sets that; groups/categories have no status.

Loop 1 (`learn_from_inward`) will share `catalogue.common`; this module is
deliberately thin so the two stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map
from app.domain.product_parse import parse_product_line
from app.models import InvoiceLine, Item, ItemCategory
from app.models._mixins import AliasSource
from app.services.catalogue.common import resolve_or_create_group, write_alias


@dataclass
class LearnResult:
    learned_group_ids: list[str] = field(default_factory=list)
    created_category_ids: list[str] = field(default_factory=list)
    written_alias_ids: list[str] = field(default_factory=list)
    attached_item_ids: list[str] = field(default_factory=list)


def learn_from_invoice(
    session: Session,
    tenant_id: str,
    lines: list[InvoiceLine],
    *,
    created_item_ids: set[str],
    now: datetime,
) -> LearnResult:
    result = LearnResult()
    synonyms = load_synonym_map(session, tenant_id)
    brands = [
        c.name
        for c in session.scalars(
            select(ItemCategory).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    ]

    for line in lines:
        if line.item_id is None:
            continue
        item = session.get(Item, line.item_id)
        if item is None:
            continue

        description = (line.description or "").strip()
        if not description:
            continue

        is_new_item = item.id in created_item_ids
        alias_source = (
            AliasSource.auto_from_invoice if is_new_item else AliasSource.learned
        )

        parsed = parse_product_line(
            description, brands=brands, synonyms=synonyms,
            default_rate_mode=item.rate_mode,
        )

        # --- 2. group attach / backfill ---
        group_id = item.group_id
        if group_id is None:
            group_text = _group_text(parsed, description)
            res = resolve_or_create_group(
                session,
                tenant_id,
                group_text,
                parsed=parsed,
                hsn_code=line.hsn_code or item.hsn_code,
                uom=line.uom or item.uom,
                rate_mode=item.rate_mode,
                synonyms=synonyms,
            )
            if res is not None:
                group_id = res.group.id
                item.group_id = group_id
                result.attached_item_ids.append(item.id)
                if res.created:
                    result.learned_group_ids.append(group_id)
                if res.category_created and res.group.category_id:
                    result.created_category_ids.append(res.group.category_id)
                # a backfilled leaf inherits the category when it has none
                if item.category_id is None and res.group.category_id:
                    item.category_id = res.group.category_id

        # --- 3. aliases ---
        if group_id is not None:
            a = write_alias(
                session, tenant_id,
                alias_text=description,
                group_id=group_id,
                source=alias_source,
                synonyms=synonyms,
                now=now,
            )
            if a is not None:
                result.written_alias_ids.append(a.id)

        # leaf alias only when the wording differs from the item's own name
        # (an exact-name match already resolves without help)
        if description.lower() != (item.name or "").lower():
            a = write_alias(
                session, tenant_id,
                alias_text=description,
                item_id=item.id,
                source=alias_source,
                synonyms=synonyms,
                now=now,
            )
            # write_alias enforces one-key-one-row: if the group alias above
            # already claimed the key, this is a no-op returning that row.
            if a is not None and a.id not in result.written_alias_ids:
                result.written_alias_ids.append(a.id)

    return result


def _group_text(parsed: object, fallback: str) -> str:
    brand = getattr(parsed, "brand", None)
    product = getattr(parsed, "product", "")
    parts = [p for p in (brand, product) if p]
    text = " ".join(parts).strip()
    return text or fallback

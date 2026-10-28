"""Recompute `sell_price` for catalog items in one pass.

Decimal in Python (never SQL float rounding), then a single bulk UPDATE keyed by
primary key. A 2,056-item catalog reprices in well under a second.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import SupplierCatalog, SupplierCatalogGroupMargin, SupplierCatalogItem
from app.services.catalog.pricing import effective_margin, sell_price

_CHUNK = 500


def group_margin_map(session: Session, catalog_id: str) -> dict[str, Decimal]:
    """category id -> that group's margin in this catalog (groups without one are absent)."""
    G = SupplierCatalogGroupMargin
    return {
        cid: Decimal(m)
        for cid, m in session.execute(select(G.category_id, G.margin_pct).where(G.catalog_id == catalog_id))
    }


def drop_group_margins(session: Session, tenant_id: str, src_id: str, target_id: str | None) -> set[str]:
    """Category `src_id` is being merged away or deleted: remove its margins. Returns the
    catalogs whose rows change margin (the source had one there, or the target does), so the
    caller reprices them once the rows have moved."""
    G = SupplierCatalogGroupMargin
    gone = list(
        session.scalars(select(G).where(G.tenant_id == tenant_id, G.category_id == src_id))
    )
    affected = {g.catalog_id for g in gone}
    if target_id is not None:
        affected |= set(
            session.scalars(
                select(G.catalog_id).where(G.tenant_id == tenant_id, G.category_id == target_id)
            )
        )
    for g in gone:
        session.delete(g)
    session.flush()
    return affected


def reprice(
    session: Session, catalog: SupplierCatalog, item_ids: Iterable[str] | None = None
) -> int:
    """Set every item's `sell_price` from its cost and effective margin.

    `item_ids` limits the pass to those items (still scoped to `catalog`); None means
    the whole catalog. Excluded items are repriced too, so including one later shows a
    current price. Returns how many prices actually changed. Caller commits.
    """
    C = SupplierCatalogItem
    base = select(C.id, C.cost_price, C.item_margin_pct, C.sell_price, C.category_id).where(
        C.catalog_id == catalog.id, C.tenant_id == catalog.tenant_id
    )
    group_margins = group_margin_map(session, catalog.id)
    rows: list[tuple[str, Decimal, Decimal | None, Decimal, str | None]] = []
    if item_ids is None:
        rows = list(session.execute(base).tuples())
    else:
        ids = list(item_ids)
        for i in range(0, len(ids), _CHUNK):
            rows += list(session.execute(base.where(C.id.in_(ids[i : i + _CHUNK]))).tuples())

    changes: list[dict[str, object]] = []
    for item_id, cost, override, current, category_id in rows:
        margin = effective_margin(
            None if override is None else Decimal(override),
            Decimal(catalog.bulk_margin_pct),
            group_margins.get(category_id) if category_id else None,
        )
        new = sell_price(Decimal(cost), margin, catalog.rounding_step)
        if new != Decimal(current):
            changes.append({"id": item_id, "sell_price": new})

    if changes:
        session.execute(update(C), changes)
    return len(changes)

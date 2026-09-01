"""Item read-side helpers: rate-in-band, document count, search, HSN→GST fill."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import HsnCode, Item, ItemAlias

_NAME_SIMILARITY_FLOOR = 0.3


def rate_in_band(item: Item) -> bool | None:
    """None when no band or no rate; else whether default_rate is within it."""
    if item.default_rate is None or (item.price_min is None and item.price_max is None):
        return None
    r = Decimal(str(item.default_rate))
    below = item.price_min is not None and r < Decimal(str(item.price_min))
    above = item.price_max is not None and r > Decimal(str(item.price_max))
    return not (below or above)


def document_count(session: Session, item_id: str) -> int:
    """Invoice lines + inward-bill lines referencing this item.

    Written defensively — a table is only counted if it exists and has the
    expected column. Both are 0 until Sales / Inward-approve write lines.
    """
    total = 0
    for table_name, col in (
        ("invoice_line", "item_id"),
        ("inward_bill_line", "matched_item_id"),
    ):
        table = Item.metadata.tables.get(table_name)
        if table is None or col not in table.c:
            continue
        total += (
            session.scalar(
                select(func.count()).select_from(table).where(table.c[col] == item_id)
            )
            or 0
        )
    return total


def hsn_gst_rate(session: Session, code: str | None) -> Decimal | None:
    """The default GST rate for an HSN code, or None."""
    if not code:
        return None
    rate = session.scalar(
        select(HsnCode.default_gst_rate).where(HsnCode.code == code)
    )
    return Decimal(str(rate)) if rate is not None else None


def apply_search(stmt: Select, session: Session, q: str) -> Select:
    """Widen `stmt` (which selects Item) with a fuzzy name match plus substring
    on grade / size_text / HSN, ordered confirmed-first then by times_billed.
    """
    q = q.strip()
    if not q:
        return stmt

    like = f"%{q.lower()}%"
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

    alias_hit = (
        select(ItemAlias.item_id)
        .where(
            ItemAlias.item_id == Item.id,
            func.lower(ItemAlias.alias_text).like(like),
        )
        .exists()
    )
    conds = [
        func.lower(Item.name).like(like),
        func.lower(func.coalesce(Item.grade, "")).like(like),
        func.lower(func.coalesce(Item.size_text, "")).like(like),
        func.coalesce(Item.hsn_code, "").like(f"{q}%"),
        alias_hit,
    ]

    if is_pg:
        conds.append(func.similarity(Item.name_normalized, q.lower()) > _NAME_SIMILARITY_FLOOR)
        stmt = stmt.where(or_(*conds)).order_by(
            (Item.status == "confirmed").desc(),
            func.similarity(Item.name_normalized, q.lower()).desc(),
            Item.times_billed.desc(),
        )
    else:
        stmt = stmt.where(or_(*conds)).order_by(
            (Item.status == "confirmed").desc(),
            Item.times_billed.desc(),
            func.lower(Item.name),
        )
    return stmt

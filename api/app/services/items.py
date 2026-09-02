"""Item read-side helpers: rate-in-band, document count, search, HSN→GST fill."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
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


def apply_search(
    stmt: Select, session: Session, q: str, *, tenant_id: str | None = None
) -> Select:
    """Widen `stmt` (which selects Item) with:

      - raw substring on name / alias / grade / size_text / HSN prefix
      - **normalized** substring: the query is run through the same
        `normalize_name` pipeline as `item.name_normalized`, so typing a
        synonym / misspelling ("karai", "zhula") matches an item stored
        under its canonical token ("... kadai ...", "... jhula ...")
      - `word_similarity` on `name_normalized` (Postgres) as a catch-all
        for misspellings no synonym row covers — this scores the query
        against the best-matching *word* in the name, so a short query
        ("kdai") still matches a long name ("ss kadai 10"); plain
        `similarity()` would not.

    Ordered confirmed-first, then word-similarity score (PG), then
    times_billed. Pass `tenant_id` so the tenant's synonym map is applied to
    the query; without it the normalized rung falls back to a synonym-free
    normalize.
    """
    q = q.strip()
    if not q:
        return stmt

    like = f"%{q.lower()}%"
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

    syn = load_synonym_map(session, tenant_id) if tenant_id else {}
    nkey = normalize_name(q, syn)
    nlike = f"%{nkey}%" if nkey else None

    alias_hit = (
        select(ItemAlias.item_id)
        .where(
            ItemAlias.item_id == Item.id,
            or_(
                func.lower(ItemAlias.alias_text).like(like),
                *([ItemAlias.alias_normalized.like(nlike)] if nlike else []),
            ),
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
    if nlike:
        conds.append(Item.name_normalized.like(nlike))

    if is_pg:
        sim_arg = nkey or q.lower()
        # word_similarity(query, text): best-matching word in `text`, so a
        # short typed token still scores against a long item name.
        wsim = func.word_similarity(sim_arg, Item.name_normalized)
        conds.append(wsim > _NAME_SIMILARITY_FLOOR)
        stmt = stmt.where(or_(*conds)).order_by(
            (Item.status == "confirmed").desc(),
            wsim.desc(),
            Item.times_billed.desc(),
        )
    else:
        stmt = stmt.where(or_(*conds)).order_by(
            (Item.status == "confirmed").desc(),
            Item.times_billed.desc(),
            func.lower(Item.name),
        )
    return stmt

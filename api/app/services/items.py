"""Item read-side helpers: rate-in-band, document count, search, HSN→GST fill."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import HsnCode, Item, ItemAlias

# word_similarity() scores the query against the best-matching *word* in the
# name, so it runs higher than the old whole-string similarity() and needs a
# stricter floor. 0.60 rejects unrelated bridges ("topia"->"toaster" ~0.33)
# while keeping close typos ("kdai"->"kadai", "katri"->"katori"). A rare
# near-miss ("lota"->"lotion" ~0.6) can still show up — it's one extra
# dropdown row, and the real narrowing tool is a second search word (the
# AND-token rung below), not a tighter fuzzy floor.
_WORD_SIMILARITY_FLOOR = 0.60

# Hard ceiling on rows a fuzzy search returns. The result is ranked by a
# non-deterministic trigram score, so there is no stable keyset to page on;
# past ~50 the extra rows are noise anyway. Narrow with a second word.
SEARCH_RESULT_CAP = 50


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
    ntokens = [t for t in nkey.split(" ") if t] if nkey else []
    multi = len(ntokens) > 1

    # --- the normalized rung: AND across query tokens so more words NARROW ---
    # Each normalized token must appear in name_normalized (or an alias). A
    # single-token query is the common "type a name" case; a two-token query
    # ("topia steel") should only match items carrying *both*.
    def _tokens_all_in(col):  # type: ignore[no-untyped-def]
        return and_(*[col.like(f"%{t}%") for t in ntokens]) if ntokens else None

    name_norm_hit = _tokens_all_in(Item.name_normalized)
    alias_norm_hit = _tokens_all_in(ItemAlias.alias_normalized)

    alias_exists = (
        select(ItemAlias.item_id)
        .where(
            ItemAlias.item_id == Item.id,
            or_(
                func.lower(ItemAlias.alias_text).like(like),
                *([alias_norm_hit] if alias_norm_hit is not None else []),
            ),
        )
        .exists()
    )
    conds = [
        func.lower(Item.name).like(like),
        func.lower(func.coalesce(Item.grade, "")).like(like),
        func.lower(func.coalesce(Item.size_text, "")).like(like),
        func.coalesce(Item.hsn_code, "").like(f"{q}%"),
        alias_exists,
    ]
    if name_norm_hit is not None:
        conds.append(name_norm_hit)

    if is_pg:
        # Fuzzy catch-all for a *single* mistyped token only. On a multi-token
        # query the AND-substring rung above already handles narrowing, and
        # word_similarity against one word would re-widen it — so skip it.
        if not multi:
            sim_arg = nkey or q.lower()
            wsim = func.word_similarity(sim_arg, Item.name_normalized)
            conds.append(wsim > _WORD_SIMILARITY_FLOOR)
            order_sim = wsim
        else:
            # rank by how well the whole phrase matches the whole name
            order_sim = func.similarity(nkey, Item.name_normalized)
        stmt = stmt.where(or_(*conds)).order_by(
            (Item.status == "confirmed").desc(),
            order_sim.desc(),
            Item.times_billed.desc(),
        )
    else:
        stmt = stmt.where(or_(*conds)).order_by(
            (Item.status == "confirmed").desc(),
            Item.times_billed.desc(),
            func.lower(Item.name),
        )
    return stmt

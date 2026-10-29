"""Item read-side helpers: rate-in-band, document count, search, HSN→GST fill."""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import Select, String, and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.domain.translit import devanagari_to_roman
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


def document_counts(session: Session, item_ids: list[str]) -> dict[str, int]:
    """`document_count` for many items in a few grouped queries instead of two per item."""
    out: dict[str, int] = {}
    for table_name, col in (
        ("invoice_line", "item_id"),
        ("inward_bill_line", "matched_item_id"),
    ):
        table = Item.metadata.tables.get(table_name)
        if table is None or col not in table.c:
            continue
        for i in range(0, len(item_ids), 500):
            chunk = item_ids[i : i + 500]
            for iid, n in session.execute(
                select(table.c[col], func.count())
                .where(table.c[col].in_(chunk))
                .group_by(table.c[col])
            ):
                out[iid] = out.get(iid, 0) + n
    return out


def detach_catalog_links(
    session: Session, item_ids: list[str], *, to_item_id: str | None = None
) -> None:
    """An item is being deleted (`to_item_id` None) or merged into another: point the price-list
    products and rows that referenced it at the other item, or clear the link. Without this the
    delete fails on the foreign key and a merge leaves the products pointing at a hidden item."""
    from sqlalchemy import update

    from app.models import (
        CatalogProduct,
        CustomerOrderLine,
        SupplierCatalogItem,
        SupplierPricePoint,
    )

    for i in range(0, len(item_ids), 500):
        chunk = item_ids[i : i + 500]
        for model in (CatalogProduct, SupplierCatalogItem, SupplierPricePoint, CustomerOrderLine):
            session.execute(
                update(model).where(model.item_id.in_(chunk)).values(item_id=to_item_id)
            )


def hsn_exists(session: Session, code: str | None) -> bool:
    """Is this code in the HSN reference list? (`item.hsn_code` is a foreign key to it.)"""
    if not code:
        return False
    return session.scalar(select(HsnCode.code).where(HsnCode.code == code)) is not None


def hsn_gst_rate(session: Session, code: str | None) -> Decimal | None:
    """The default GST rate for an HSN code, or None."""
    if not code:
        return None
    rate = session.scalar(
        select(HsnCode.default_gst_rate).where(HsnCode.code == code)
    )
    return Decimal(str(rate)) if rate is not None else None


_TOKEN_RE = re.compile(r"\d+|[a-z]+")
# a decimal or fraction typed as one measure: 1.5, 0.75, 1/2, 3/4
_DECIMAL_RE = re.compile(r"\d+(?:[./]\d+)+")
# shortest word that gets typo tolerance inside a multi-word query
_FUZZY_MIN_LEN = 4


def _search_tokens(nkey: str) -> list[str]:
    """Query tokens for the AND rung. Splits digit/letter glue ("10kg" -> 10, kg;
    "ss304" -> ss, 304) so it finds "10 kg" / "SS 304" and the other way round,
    and drops the "x" in a dimension ("30 x 26" -> 30, 26): the stored side may
    spell the size without it ("30x26" in size_text becomes "30 26")."""
    toks = _TOKEN_RE.findall(nkey)
    if len(toks) > 1:
        toks = [t for t in toks if t != "x"] or toks
    return toks


# Spellings of one unit that shops mix freely. A query word finds any of them, so
# "12 inch" finds "12 inches" / "12 in" with no change to stored names.
_UNIT_GROUPS = [
    {"in", "inch", "inches"},
    {"gm", "gms", "gram", "grams"},
    {"ltr", "litre", "liter", "litres", "liters"},
    {"ft", "feet", "foot"},
    {"cm", "centimeter", "centimetre"},
    {"kg", "kgs", "kilo", "kilogram"},
    {"mtr", "meter", "metre", "meters", "metres"},
    {"nos", "pcs", "pc", "piece", "pieces"},
    {"dozen", "doz", "dz"},
]


def _word_variants(t: str) -> list[str]:
    """The query word plus its unit spellings and its singular ("plates" -> "plate")."""
    out = {t}
    for g in _UNIT_GROUPS:
        if t in g:
            out |= g
    if len(t) >= 5 and t.endswith("es") and t[:-2] not in out:
        out.add(t[:-2])
    if len(t) >= 4 and t.endswith("s") and not t.endswith("ss"):
        out.add(t[:-1])
    return sorted(out)


def _clean_col(col, is_pg: bool):  # type: ignore[no-untyped-def]
    """Lower-cased column with every non-alphanumeric run turned into a space (Postgres);
    the SQLite test path only lower-cases."""
    c = func.lower(func.coalesce(col, ""))
    return func.regexp_replace(c, "[^a-z0-9]+", " ", "g") if is_pg else c


def apply_search(
    stmt: Select, session: Session, q: str, *, tenant_id: str | None = None
) -> Select:
    """Widen `stmt` (which selects Item) with:

      - raw substring on name / alias / grade / size_text / HSN prefix / code prefix
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
    # Devanagari typed into the box ("कढ़ाई"): search on its Roman spelling,
    # which the synonym map and trigram rung then reconcile with the catalogue.
    raw_q = q
    q = devanagari_to_roman(q).strip()
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
    # a 1-2 letter query ("in", "ss") is a whole word, not a substring of "tin"/"stainless"
    short_word = is_pg and len(q) <= 2 and q.isalpha()

    syn = load_synonym_map(session, tenant_id) if tenant_id else {}
    # "1.5" / "1/2" are one measure, not two numbers: lift them out of the
    # query and require their digits side by side (stored "1.5 mm" normalizes
    # to the tokens "1 5 mm"). Without this "1.5" matched any 1 and 5.
    decimals = [re.findall(r"\d+", m.group(0)) for m in _DECIMAL_RE.finditer(q)]
    nkey = normalize_name(_DECIMAL_RE.sub(" ", q), syn)
    ntokens = _search_tokens(nkey)
    multi = len(ntokens) + len(decimals) > 1 or bool(decimals)

    # --- the normalized rung: AND across query tokens so more words NARROW ---
    # Each normalized token must appear in the item's searchable text (or an
    # alias). A single-token query is the common "type a name" case; a
    # two-token query ("topia steel") should only match items carrying *both*.
    #
    # The searchable text is the normalized name PLUS the size and grade
    # columns (a Tally import often holds "Tray" + size_text "30x26"), with
    # separators turned to spaces so "30x26", "30 x 26" and "30*26" are all the
    # tokens 30, 26. On Postgres a numeric token must start at a digit
    # boundary ("30" finds "30x26" / "ss304" but not "130" or "300 ml").
    hay = (
        literal(" ", String)
        + Item.name_normalized
        + " "
        + _clean_col(Item.size_text, is_pg)
        + " "
        + _clean_col(Item.grade, is_pg)
    )

    def _word_like(col, v):  # type: ignore[no-untyped-def]
        # 1-2 letter words ("in", "ss", "mm") must stand alone; a digit beside
        # them is fine ("ss304", "10mm")
        if is_pg and len(v) <= 2:
            return col.op("~")(f"(^|[^a-z]){v}([^a-z]|$)")
        return col.like(f"%{v}%")

    def _tok_hit(col, t):  # type: ignore[no-untyped-def]
        if is_pg and t.isdigit():
            return col.op("~")(f"(^|[^0-9]){t}")
        # the word, its unit spellings ("inch"/"inches"/"in") and its singular
        hit = or_(*[_word_like(col, v) for v in _word_variants(t)])
        # a mistyped word in a multi-word query ("topia stel") still has to
        # match *something*: allow a near-miss on words long enough to be
        # unambiguous. The AND across tokens still narrows the result.
        if is_pg and multi and len(t) >= _FUZZY_MIN_LEN:
            return or_(hit, func.word_similarity(t, col) > _WORD_SIMILARITY_FLOOR)
        return hit

    def _phrase_hit(nums: list[str]):  # type: ignore[no-untyped-def]
        s = " ".join(nums)
        if is_pg:
            return hay.op("~")(f"(^|[^0-9]){s}([^0-9]|$)")
        return hay.like(f"%{s}%")

    token_hits = [_tok_hit(hay, t) for t in ntokens] + [_phrase_hit(d) for d in decimals]
    name_norm_hit = and_(*token_hits) if token_hits else None
    alias_norm_hit = (
        and_(*[ItemAlias.alias_normalized.like(f"%{t}%") for t in ntokens])
        if ntokens
        else None
    )
    # Items whose numbers sit together in query order ("30 26" -> "30 x 26")
    # rank above items that merely contain both numbers somewhere.
    nums = [t for t in ntokens if t.isdigit()]
    dims_adjacent = None
    if len(nums) > 1:
        dims_adjacent = or_(
            hay.like("%" + " ".join(nums) + "%"),
            hay.like("%" + " x ".join(nums) + "%"),
        )

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
    raw_cols = [
        func.lower(Item.name),
        func.lower(func.coalesce(Item.grade, "")),
        func.lower(func.coalesce(Item.size_text, "")),
    ]
    conds = [
        *(
            [_word_like(c, q.lower()) for c in raw_cols]
            if short_word
            else [c.like(like) for c in raw_cols]
        ),
        *([c.like(f"%{q.lower()}%") for c in raw_cols] if raw_q != q and not short_word else []),
        func.coalesce(Item.hsn_code, "").like(f"{q}%"),
        # the item's code, so a scanned barcode or a typed code finds it
        func.coalesce(Item.sku, "").like(f"{q}%"),
        func.coalesce(Item.barcode, "").like(f"{q}%"),
        alias_exists,
    ]
    if name_norm_hit is not None:
        conds.append(name_norm_hit)
    adjacency = (
        [case((dims_adjacent, 0), else_=1)] if dims_adjacent is not None else []
    )
    # Relevance tier, ahead of "confirmed": the item named exactly what was
    # typed, then one whose name starts with it, then the rest. Otherwise a
    # confirmed loose match buried the exact one.
    tiers = (
        [
            case(
                (Item.name_normalized == nkey, 0),
                (Item.name_normalized.like(f"{nkey}%"), 1),
                else_=2,
            )
        ]
        if nkey
        else []
    )

    if is_pg:
        # Fuzzy catch-all for a *single* mistyped token only. On a multi-token
        # query the AND-substring rung above already handles narrowing, and
        # word_similarity against one word would re-widen it — so skip it.
        if not multi and len(nkey or q) > 2:
            sim_arg = nkey or q.lower()
            wsim = func.word_similarity(sim_arg, Item.name_normalized)
            conds.append(wsim > _WORD_SIMILARITY_FLOOR)
            order_sim = wsim
        else:
            # rank by how well the whole phrase matches the whole name
            order_sim = func.similarity(nkey or q.lower(), Item.name_normalized)
        stmt = stmt.where(or_(*conds)).order_by(
            *tiers,
            (Item.status == "confirmed").desc(),
            *adjacency,
            order_sim.desc(),
            Item.times_billed.desc(),
        )
    else:
        stmt = stmt.where(or_(*conds)).order_by(
            *tiers,
            (Item.status == "confirmed").desc(),
            *adjacency,
            Item.times_billed.desc(),
            func.lower(Item.name),
        )
    return stmt

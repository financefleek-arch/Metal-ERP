"""Match a parsed Tally stock item against the catalogue.

Ladder (stop at the first hit):
  1. GUID   item.tally_guid == this GUID  -> update blank fields only;
            if nothing to fill -> "skip" (re-import is a no-op)
  2. name   normalize(NAME) == an item.name_normalized (not archived/merged)
            -> link + set tally_guid + update-blanks
            (a single strong trigram near-match, Postgres, -> FLAG, not link)
  3. none   -> create new  (source=import, status=unconfirmed)

Validation flags exclude a row from commit until resolved:
  bad_hsn (not in reference), name_too_long, name_bad_chars,
  name_near_match, duplicate_guid_in_file
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.normalize import normalize_name
from app.models import HsnCode, Item
from app.models._mixins import ItemStatus
from app.reference import LEGAL_NAME_MAX, validate_item_name
from tools.tally_import.parser import TallyStockItem

_NAME_SIM_FLOOR = 0.82

# A bulk import prefetches the whole catalogue and matches in memory rather
# than issuing four queries per row. Above this many staged rows the trigram
# near-match query is also skipped (exact GUID/name only) to keep the upload
# under a gateway timeout; the reviewer can still edit names afterwards.
_BULK_TRIGRAM_CEILING = 800


@dataclass
class ItemMatchResult:
    method: str  # guid | name | none
    item_id: str | None = None
    fillable: bool = True  # for method=guid: is there any blank to fill?
    flags: list[dict] = field(default_factory=list)


def _flag(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _has_blank_to_fill(item: Item, si: TallyStockItem, hsn_ok: str | None) -> bool:
    return bool(
        (si.base_units and not item.uom)
        or (hsn_ok and not item.hsn_code)
        or (si.standard_rate is not None and item.default_rate is None)
        or (si.gst_rate is not None and float(item.gst_rate or 0) == 0.0)
    )


@dataclass
class _ItemSnap:
    """The four fields _has_blank_to_fill needs, without holding an ORM row."""

    id: str
    uom: str | None
    hsn_code: str | None
    default_rate: object | None
    gst_rate: object | None


def _snap_has_blank(snap: _ItemSnap, si: TallyStockItem, hsn_ok: str | None) -> bool:
    return bool(
        (si.base_units and not snap.uom)
        or (hsn_ok and not snap.hsn_code)
        or (si.standard_rate is not None and snap.default_rate is None)
        or (si.gst_rate is not None and float(snap.gst_rate or 0) == 0.0)
    )


def match_stock_items_bulk(
    session: Session,
    tenant_id: str,
    items: list[TallyStockItem],
    *,
    guids_in_file: dict[str, int],
    synonyms: dict[str, str],
) -> list[ItemMatchResult]:
    """Match a whole file at once.

    Three prefetch queries (HSN reference, catalogue by GUID, catalogue by
    normalized name) instead of ~4 per row. The trigram near-match is run
    per unmatched row only while the file is small (see _BULK_TRIGRAM_CEILING).
    Order of results matches `items`.
    """
    # --- prefetch: HSN reference codes present in the file ---
    file_hsns = {si.hsn.strip() for si in items if si.hsn and si.hsn.strip()}
    known_hsns: set[str] = set()
    if file_hsns:
        known_hsns = set(
            session.scalars(
                select(HsnCode.code).where(HsnCode.code.in_(file_hsns))
            ).all()
        )

    # --- prefetch: catalogue, indexed by guid and by normalized name ---
    by_guid: dict[str, _ItemSnap] = {}
    by_name: dict[str, _ItemSnap] = {}
    rows = session.execute(
        select(
            Item.id,
            Item.tally_guid,
            Item.name_normalized,
            Item.status,
            Item.merged_into_id,
            Item.uom,
            Item.hsn_code,
            Item.default_rate,
            Item.gst_rate,
        ).where(Item.tenant_id == tenant_id)
    ).all()
    for r in rows:
        if r.merged_into_id is not None:
            continue
        snap = _ItemSnap(r.id, r.uom, r.hsn_code, r.default_rate, r.gst_rate)
        if r.tally_guid:
            by_guid.setdefault(r.tally_guid, snap)
        if r.name_normalized and r.status != ItemStatus.archived:
            by_name.setdefault(r.name_normalized, snap)

    run_trigram = (
        len(items) <= _BULK_TRIGRAM_CEILING
        and session.bind is not None
        and session.bind.dialect.name == "postgresql"
    )

    results: list[ItemMatchResult] = []
    for si in items:
        flags: list[dict] = []
        name = (si.name or "").strip()
        if len(name) > LEGAL_NAME_MAX:
            flags.append(
                _flag("name_too_long", f"Name is {len(name)} chars (max {LEGAL_NAME_MAX})")
            )
        else:
            try:
                validate_item_name(name)
            except ValueError as e:
                flags.append(_flag("name_bad_chars", str(e)))

        hsn_ok: str | None = None
        if si.hsn and si.hsn.strip():
            code = si.hsn.strip()
            if code in known_hsns:
                hsn_ok = code
            else:
                flags.append(_flag("bad_hsn", f"HSN {code} not in the reference list"))

        if si.guid and guids_in_file.get(si.guid, 0) > 1:
            flags.append(
                _flag("duplicate_guid_in_file", "This GUID appears more than once in the file")
            )

        # --- 1: GUID ---
        if si.guid and si.guid in by_guid:
            snap = by_guid[si.guid]
            results.append(
                ItemMatchResult(
                    "guid",
                    snap.id,
                    fillable=_snap_has_blank(snap, si, hsn_ok),
                    flags=flags,
                )
            )
            continue

        # --- 2: exact normalized name ---
        key = normalize_name(name, synonyms)
        if key and key in by_name:
            results.append(ItemMatchResult("name", by_name[key].id, flags=flags))
            continue

        # --- 2b: trigram near-match (small files only) ---
        if key and run_trigram:
            near = session.execute(
                select(Item.id, func.similarity(Item.name_normalized, key).label("s"))
                .where(
                    Item.tenant_id == tenant_id,
                    Item.status != ItemStatus.archived,
                    Item.merged_into_id.is_(None),
                    func.similarity(Item.name_normalized, key) >= _NAME_SIM_FLOOR,
                )
                .order_by(func.similarity(Item.name_normalized, key).desc())
                .limit(2)
            ).all()
            if len(near) == 1:
                flags.append(
                    _flag("name_near_match", f"Looks like an existing item ({near[0].s:.0%})")
                )
                results.append(ItemMatchResult("name", near[0].id, flags=flags))
                continue

        # --- 3: none ---
        results.append(ItemMatchResult("none", None, flags=flags))

    return results


def match_stock_item(
    session: Session,
    tenant_id: str,
    si: TallyStockItem,
    *,
    guids_in_file: dict[str, int],
    synonyms: dict[str, str],
) -> ItemMatchResult:
    flags: list[dict] = []

    name = (si.name or "").strip()
    if len(name) > LEGAL_NAME_MAX:
        flags.append(_flag("name_too_long", f"Name is {len(name)} chars (max {LEGAL_NAME_MAX})"))
    else:
        try:
            validate_item_name(name)
        except ValueError as e:
            flags.append(_flag("name_bad_chars", str(e)))

    hsn_ok: str | None = None
    if si.hsn and si.hsn.strip():
        code = si.hsn.strip()
        exists = session.scalar(select(HsnCode).where(HsnCode.code == code))
        if exists is not None:
            hsn_ok = code
        else:
            flags.append(_flag("bad_hsn", f"HSN {code} not in the reference list"))

    if si.guid and guids_in_file.get(si.guid, 0) > 1:
        flags.append(
            _flag("duplicate_guid_in_file", "This GUID appears more than once in the file")
        )

    # --- 1: GUID ---
    if si.guid:
        hit = session.scalar(
            select(Item).where(
                Item.tenant_id == tenant_id,
                Item.tally_guid == si.guid,
                Item.merged_into_id.is_(None),
            )
        )
        if hit is not None:
            return ItemMatchResult(
                "guid",
                hit.id,
                fillable=_has_blank_to_fill(hit, si, hsn_ok),
                flags=flags,
            )

    # --- 2: normalized name ---
    key = normalize_name(name, synonyms)
    if key:
        exact = session.scalar(
            select(Item).where(
                Item.tenant_id == tenant_id,
                Item.name_normalized == key,
                Item.status != ItemStatus.archived,
                Item.merged_into_id.is_(None),
            )
        )
        if exact is not None:
            return ItemMatchResult("name", exact.id, flags=flags)

        is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
        if is_pg:
            rows = session.execute(
                select(Item.id, func.similarity(Item.name_normalized, key).label("s"))
                .where(
                    Item.tenant_id == tenant_id,
                    Item.status != ItemStatus.archived,
                    Item.merged_into_id.is_(None),
                    func.similarity(Item.name_normalized, key) >= _NAME_SIM_FLOOR,
                )
                .order_by(func.similarity(Item.name_normalized, key).desc())
                .limit(2)
            ).all()
            if len(rows) == 1:
                flags.append(
                    _flag("name_near_match", f"Looks like an existing item ({rows[0].s:.0%})")
                )
                return ItemMatchResult("name", rows[0].id, flags=flags)

    # --- 3: none ---
    return ItemMatchResult("none", None, flags=flags)

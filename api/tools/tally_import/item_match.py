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
from app.reference import LEGAL_NAME_MAX, validate_legal_name
from tools.tally_import.parser import TallyStockItem

_NAME_SIM_FLOOR = 0.82


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
            validate_legal_name(name)
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

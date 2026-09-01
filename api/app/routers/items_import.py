"""Bulk item import from a Tally Prime stock-items XML.

Flow: upload -> parse (+ name-parse each) into staging_tally_item ->
review (GUID -> name -> new) -> per-row adjust -> commit ready rows into
`item` (seeding hsn_code for an unseen HSN). Mirrors parties_import.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.deps import SessionDep, WriteUser
from app.domain.normalize import load_synonym_map, normalize_name
from app.domain.product_parse import parse_product_line
from app.models import HsnCode, Item, ItemCategory, ProductGroup, StagingTallyItem
from app.models._mixins import ItemSource, ItemStatus, ItemType, RateMode
from app.services.item_resolution import resolve_group
from tools.tally_import.item_match import match_stock_items_bulk
from tools.tally_import.parser import is_zero_history_dummy, parse_stock_items

router = APIRouter(prefix="/api/items/import", tags=["items-import"])

# A full-catalogue TallyPrime "All Masters" export runs 30-40 MB (UTF-16,
# every stock item with its GST/HSN detail lists). Only STOCKITEM nodes are
# read, so the parse stays fast; the cap just needs headroom.
_MAX_BYTES = 64 * 1024 * 1024
Outcome = Literal["new", "link", "skip", "flag"]


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


class StockGroupCount(BaseModel):
    name: str
    item_count: int


class ImportBatchOut(BaseModel):
    batch_id: str
    total: int
    dummies_skipped: int
    groups: list[StockGroupCount]


class ParsedAttrs(BaseModel):
    metal: str | None
    shape: str | None
    grade: str | None
    size_text: str | None
    sku: str | None


class StagedRowOut(BaseModel):
    id: str
    stock_name: str
    parent_group: str | None
    base_units: str | None
    hsn: str | None
    gst_rate: str | None
    standard_rate: str | None
    item_type: ItemType
    rate_mode: RateMode
    parsed: ParsedAttrs
    outcome: Outcome
    match_item_id: str | None
    match_item_name: str | None
    decision: str
    edited_name: str | None
    seed_hsn: bool
    flags: list[dict]


class ReviewOut(BaseModel):
    batch_id: str
    counts: dict[str, int]
    rows: list[StagedRowOut]


class RowPatch(BaseModel):
    type_override: ItemType | None = None
    decision: Literal["pending", "create", "link", "skip"] | None = None
    edited_name: str | None = Field(default=None, max_length=200)
    seed_hsn: bool | None = None


class CommitOut(BaseModel):
    created: int
    updated: int
    skipped: int
    still_flagged: int
    hsn_seeded: int
    groups_created: int


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_MRP_UNITS = {"nos", "pcs", "pc", "set", "no"}


def _map_uom(base_units: str | None) -> str | None:
    if not base_units:
        return None
    return base_units.strip().lower() or None


def _proposed_type(base_units: str | None) -> ItemType:
    u = (base_units or "").strip().lower()
    return ItemType.mrp if u in _MRP_UNITS else ItemType.bulk


def _effective_type(row: StagingTallyItem) -> ItemType:
    return ItemType(row.type_override) if row.type_override else row.proposed_type


def _effective_name(row: StagingTallyItem) -> str:
    return (row.edited_name or row.stock_name).strip()


def _outcome(row: StagingTallyItem) -> Outcome:
    if row.decision == "skip":
        return "skip"
    flags = row.flags_json or []
    # opting into seeding clears the bad-HSN blocker
    non_near = [f.get("code") for f in flags if f.get("code") != "name_near_match"]
    if row.seed_hsn:
        non_near = [c for c in non_near if c != "bad_hsn"]
    has_blocking = bool(non_near)
    has_near = any(f.get("code") == "name_near_match" for f in flags)

    if row.decision == "create":
        return "flag" if has_blocking else "new"
    if row.decision == "link":
        return "flag" if has_blocking else "link"

    # decision == "pending" — the auto outcome
    if has_blocking or has_near:
        return "flag"
    if row.match_method == "guid":
        return "link" if row.guid_fillable else "skip"
    if row.match_method == "name":
        return "link"
    return "new"


def _rows(session: SessionDep, tenant_id: str, batch_id: str) -> list[StagingTallyItem]:
    rows = list(
        session.scalars(
            select(StagingTallyItem)
            .where(
                StagingTallyItem.tenant_id == tenant_id,
                StagingTallyItem.batch_id == batch_id,
            )
            .order_by(StagingTallyItem.stock_name)
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Import batch not found")
    return rows


def _row_out(session: SessionDep, r: StagingTallyItem) -> StagedRowOut:
    name = None
    if r.match_item_id:
        it = session.get(Item, r.match_item_id)
        name = it.name if it else None
    return StagedRowOut(
        id=r.id,
        stock_name=r.stock_name,
        parent_group=r.parent_group,
        base_units=r.base_units,
        hsn=r.hsn,
        gst_rate=str(r.gst_rate) if r.gst_rate is not None else None,
        standard_rate=str(r.standard_rate) if r.standard_rate is not None else None,
        item_type=_effective_type(r),
        rate_mode=r.proposed_rate_mode,
        parsed=ParsedAttrs(
            metal=r.parsed_metal,
            shape=r.parsed_shape,
            grade=r.parsed_grade,
            size_text=r.parsed_size_text,
            sku=r.parsed_sku,
        ),
        outcome=_outcome(r),
        match_item_id=r.match_item_id,
        match_item_name=name,
        decision=r.decision,
        edited_name=r.edited_name,
        seed_hsn=r.seed_hsn,
        flags=r.flags_json or [],
    )


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@router.post("", response_model=ImportBatchOut, status_code=status.HTTP_201_CREATED)
async def upload(
    user: WriteUser,
    session: SessionDep,
    file: UploadFile = File(...),
    seed_all_hsn: bool = False,
) -> ImportBatchOut:
    """Stage a Tally stock-items XML for review.

    `seed_all_hsn` pre-arms every row to add its HSN to the reference table
    on commit (code + the parsed GST rate). Useful for a first full-catalogue
    import, where none of the shop's HSN codes are in the reference list yet.
    """
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File is larger than {_MAX_BYTES // (1024 * 1024)} MB"
        )
    try:
        stock = parse_stock_items(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not parse the Tally XML: {e}") from e
    if not stock.items:
        raise HTTPException(status_code=422, detail="No stock items found in the file")

    synonyms = load_synonym_map(session, user.tenant_id)
    brands = [
        c.name
        for c in session.scalars(
            select(ItemCategory).where(ItemCategory.tenant_id == user.tenant_id)
        ).all()
    ]

    guids_in_file: dict[str, int] = {}
    for si in stock.items:
        if si.guid:
            guids_in_file[si.guid] = guids_in_file.get(si.guid, 0) + 1

    batch_id = str(uuid.uuid4())
    grp_counts: dict[str, int] = {}
    staged = 0

    kept = [si for si in stock.items if not is_zero_history_dummy(si)]
    dummies = len(stock.items) - len(kept)

    # A TallyPrime STOCKITEM node is ~7 KB of mostly-unused tax boilerplate;
    # nothing reads raw_xml back, so keep only a debugging prefix rather than
    # staging ~16 MB for a full-catalogue import.
    _RAW_XML_KEEP = 2000

    matches = match_stock_items_bulk(
        session, user.tenant_id, kept, guids_in_file=guids_in_file, synonyms=synonyms
    )

    for si, mr in zip(kept, matches, strict=True):
        top = (si.parent or "(ungrouped)").strip() or "(ungrouped)"
        grp_counts[top] = grp_counts.get(top, 0) + 1

        p = parse_product_line(si.name, brands=brands, synonyms=synonyms)
        session.add(
            StagingTallyItem(
                tenant_id=user.tenant_id,
                batch_id=batch_id,
                tally_guid=si.guid,
                stock_name=si.name,
                parent_group=si.parent,
                base_units=si.base_units,
                hsn=si.hsn,
                gst_rate=si.gst_rate,
                standard_rate=si.standard_rate,
                raw_xml=(si.raw_xml or "")[:_RAW_XML_KEEP] or None,
                proposed_type=_proposed_type(si.base_units),
                proposed_uom=_map_uom(si.base_units),
                proposed_rate_mode=(p.rate_mode or RateMode.piece),
                parsed_metal=p.brand if p.brand in {"MS", "SS", "GI"} else p.brand,
                parsed_shape=p.product or None,
                parsed_grade=None,
                parsed_size_text=p.size,
                parsed_sku=p.sku,
                match_method=mr.method,
                match_item_id=mr.item_id,
                guid_fillable=mr.fillable,
                flags_json=mr.flags or None,
                seed_hsn=bool(seed_all_hsn and si.hsn and si.hsn.strip()),
            )
        )
        staged += 1

    session.flush()
    groups = [
        StockGroupCount(name=n, item_count=c)
        for n, c in sorted(grp_counts.items(), key=lambda kv: -kv[1])
    ]
    return ImportBatchOut(
        batch_id=batch_id, total=staged, dummies_skipped=dummies, groups=groups
    )


@router.get("/{batch_id}", response_model=ReviewOut)
def review(batch_id: str, user: WriteUser, session: SessionDep) -> ReviewOut:
    rows = _rows(session, user.tenant_id, batch_id)
    counts = {"new": 0, "link": 0, "skip": 0, "flag": 0}
    out_rows: list[StagedRowOut] = []
    for r in rows:
        ro = _row_out(session, r)
        counts[ro.outcome] += 1
        out_rows.append(ro)
    return ReviewOut(batch_id=batch_id, counts=counts, rows=out_rows)


@router.patch("/{batch_id}/rows/{row_id}", response_model=StagedRowOut)
def patch_row(
    batch_id: str,
    row_id: str,
    body: RowPatch,
    user: WriteUser,
    session: SessionDep,
) -> StagedRowOut:
    row = session.scalar(
        select(StagingTallyItem).where(
            StagingTallyItem.id == row_id,
            StagingTallyItem.batch_id == batch_id,
            StagingTallyItem.tenant_id == user.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")
    data = body.model_dump(exclude_unset=True)
    if "type_override" in data:
        row.type_override = data["type_override"].value if data["type_override"] else None
    if "decision" in data:
        row.decision = data["decision"]
    if "seed_hsn" in data:
        row.seed_hsn = bool(data["seed_hsn"])
    if "edited_name" in data:
        row.edited_name = (data["edited_name"] or "").strip() or None
        _name_flags = {"name_too_long", "name_bad_chars"}
        row.flags_json = [
            f for f in (row.flags_json or []) if f.get("code") not in _name_flags
        ] or None
    session.flush()
    return _row_out(session, row)


def _resolve_or_create_group(
    session: SessionDep,
    tenant_id: str,
    group_name: str,
    synonyms: dict[str, str],
    item_type: ItemType,
    rate_mode: RateMode,
    hsn: str | None,
    *,
    counter: list[int],
) -> str | None:
    """Map a Tally Stock Group name to a product_group, creating it if new.
    The category is guessed from the first token of the group name against
    the tenant's categories (case-insensitive prefix), else left null.
    """
    name = (group_name or "").strip()
    if not name or name.lower() in {"primary", "(ungrouped)"}:
        return None

    m = resolve_group(session, tenant_id, name, synonyms=synonyms)
    if m.group_id is not None:
        return m.group_id

    cats = {
        c.name.lower(): c.id
        for c in session.scalars(
            select(ItemCategory).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    }
    first = name.split()[0].lower() if name.split() else ""
    category_id = cats.get(name.lower()) or cats.get(first)
    # also try "a category name that starts with the group's first token"
    if category_id is None and first:
        for cname, cid in cats.items():
            if cname.startswith(first) or first.startswith(cname):
                category_id = cid
                break

    grp = ProductGroup(
        tenant_id=tenant_id,
        name=name,
        name_normalized=normalize_name(name, synonyms),
        category_id=category_id,
        item_type=item_type,
        default_rate_mode=rate_mode,
        hsn_code=hsn,
    )
    session.add(grp)
    session.flush()
    counter[0] += 1
    return grp.id


@router.post("/{batch_id}/commit", response_model=CommitOut)
def commit(batch_id: str, user: WriteUser, session: SessionDep) -> CommitOut:
    rows = _rows(session, user.tenant_id, batch_id)
    synonyms = load_synonym_map(session, user.tenant_id)
    created = updated = skipped = still_flagged = hsn_seeded = 0
    groups_created = [0]

    committable = [
        r for r in rows if not r.committed_as and _outcome(r) in {"new", "link"}
    ]

    # --- HSN pre-pass: one query for the whole batch, seed each new code once ---
    file_codes = {
        (r.hsn or "").strip() for r in committable if (r.hsn or "").strip()
    }
    known_codes: set[str] = set()
    if file_codes:
        known_codes = set(
            session.scalars(
                select(HsnCode.code).where(HsnCode.code.in_(file_codes))
            ).all()
        )
    # code -> a gst rate to seed with (first non-null wins)
    seed_rate: dict[str, object | None] = {}
    for r in committable:
        code = (r.hsn or "").strip()
        if code and r.seed_hsn and code not in known_codes:
            seed_rate.setdefault(code, None)
            if seed_rate[code] is None and r.gst_rate is not None:
                seed_rate[code] = r.gst_rate
    for code, rate in seed_rate.items():
        session.add(
            HsnCode(
                code=code,
                description="(from Tally import)",
                chapter=code[:2],
                default_gst_rate=rate,
            )
        )
        hsn_seeded += 1
    if seed_rate:
        session.flush()
        known_codes |= set(seed_rate)

    for row in rows:
        if row.committed_as:
            continue
        oc = _outcome(row)
        if oc == "skip":
            skipped += 1
            continue
        if oc == "flag":
            still_flagged += 1
            continue

        hsn = (row.hsn or "").strip() or None
        if hsn and hsn not in known_codes:
            hsn = None  # not in reference and not seeded -> import without HSN

        if oc == "link" and row.match_item_id:
            it = session.get(Item, row.match_item_id)
            if it is None or it.tenant_id != user.tenant_id:
                still_flagged += 1
                continue
            if row.proposed_uom and not it.uom:
                it.uom = row.proposed_uom
            if hsn and not it.hsn_code:
                it.hsn_code = hsn
            if row.standard_rate is not None and it.default_rate is None:
                it.default_rate = row.standard_rate
            if row.gst_rate is not None and float(it.gst_rate or 0) == 0.0:
                it.gst_rate = float(row.gst_rate)
            if not it.tally_guid:
                it.tally_guid = row.tally_guid
            row.committed_as = it.id
            updated += 1
        else:
            name = _effective_name(row)
            key = normalize_name(name, synonyms)
            if not key:
                still_flagged += 1
                continue
            clash = session.scalar(
                select(Item).where(
                    Item.tenant_id == user.tenant_id, Item.name_normalized == key
                )
            )
            if clash is not None:
                row.committed_as = clash.id  # dedupe race → link
                updated += 1
                continue

            group_id = _resolve_or_create_group(
                session,
                user.tenant_id,
                row.parent_group or "",
                synonyms,
                _effective_type(row),
                row.proposed_rate_mode,
                hsn,
                counter=groups_created,
            )
            grp = session.get(ProductGroup, group_id) if group_id else None

            it = Item(
                tenant_id=user.tenant_id,
                name=name,
                name_normalized=key,
                item_type=_effective_type(row),
                group_id=group_id,
                category_id=grp.category_id if grp else None,
                uom=row.proposed_uom,
                hsn_code=hsn,
                rate_mode=row.proposed_rate_mode,
                metal=row.parsed_metal,
                shape=row.parsed_shape,
                size_text=row.parsed_size_text,
                size_label=row.parsed_size_text,
                sku=row.parsed_sku,
                default_rate=row.standard_rate,
                gst_rate=float(row.gst_rate) if row.gst_rate is not None else 0.0,
                source=ItemSource.import_,
                status=ItemStatus.unconfirmed,
                tally_guid=row.tally_guid,
            )
            session.add(it)
            session.flush()
            row.committed_as = it.id
            created += 1

    return CommitOut(
        created=created,
        updated=updated,
        skipped=skipped,
        still_flagged=still_flagged,
        hsn_seeded=hsn_seeded,
        groups_created=groups_created[0],
    )


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard(batch_id: str, user: WriteUser, session: SessionDep) -> None:
    _rows(session, user.tenant_id, batch_id)
    session.execute(
        delete(StagingTallyItem).where(
            StagingTallyItem.tenant_id == user.tenant_id,
            StagingTallyItem.batch_id == batch_id,
        )
    )

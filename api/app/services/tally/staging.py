"""Parse a Tally masters XML into the existing staging tables.

Extracted verbatim from the parse->match->stage bodies of
`routers/parties_import.py::upload` and `routers/items_import.py::upload`
so the Tally Connector pull (F1a) stages rows identically to a manual file
upload. The review/commit endpoints are unchanged and unaware of the source;
`sync_job_id` (nullable) is the only new per-row field, set when the batch
came from a connector pull.

Both functions:
  * clear the tenant's un-committed staging rows first (one in-flight batch
    per tenant — a policy, not a schema rule),
  * parse, match in bulk, insert staged rows,
  * flush and return a small summary the caller turns into its response.

They do NOT create a `batch_id` — the caller passes one (a fresh uuid for an
upload; the connector reuses the same value it stamps on the sync job).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map
from app.domain.product_parse import parse_product_line
from app.domain.units import is_mrp_uom, normalize_uom
from app.models import ItemCategory, StagingTallyItem, StagingTallyParty
from app.models._mixins import ItemType, PartyRole
from app.reference import validate_gstin
from tools.tally_import.groups import GroupTree
from tools.tally_import.item_match import match_stock_items_bulk
from tools.tally_import.match import match_ledgers_bulk
from tools.tally_import.parser import (
    TallyLedger,
    is_zero_history_dummy,
    parse_masters,
    parse_stock_items,
)

# A TallyPrime LEDGER / STOCKITEM node is mostly unused address+GST
# boilerplate; nothing reads `raw_xml` back, so keep only a debugging prefix.
_RAW_XML_KEEP = 2000

# staging_tally_item column widths for the advisory parsed_* hints.
_PARSED_LIMITS = {
    "parsed_metal": 20,
    "parsed_shape": 24,
    "parsed_grade": 32,
    "parsed_size_text": 60,
    "parsed_sku": 64,
    "proposed_uom": 20,
}


# --------------------------------------------------------------------------
# parties
# --------------------------------------------------------------------------


@dataclass
class PartyGroupCount:
    name: str
    ledger_count: int
    implied_role: PartyRole | None


@dataclass
class PartyStagingSummary:
    batch_id: str
    total: int
    groups: list[PartyGroupCount] = field(default_factory=list)
    # ledger names present in the file, for tally_company.known_ledgers
    known_ledgers: list[dict] = field(default_factory=list)


class NoLedgersError(ValueError):
    """The XML parsed but had no <LEDGER> nodes."""


class NoStockItemsError(ValueError):
    """The XML parsed but had no <STOCKITEM> nodes."""


def stage_masters_xml(
    session: Session,
    tenant_id: str,
    raw: bytes,
    *,
    batch_id: str,
    sync_job_id: str | None = None,
) -> PartyStagingSummary:
    """Parse a Tally masters export and stage every trade-party ledger.

    Raises `ValueError` (subclass) on a parse failure or an empty file — the
    caller maps that to a 422 (upload) or a job error (connector).
    """
    try:
        masters = parse_masters(raw)
    except Exception as e:  # noqa: BLE001 - surface any XML problem uniformly
        raise ValueError(f"Could not parse the Tally XML: {e}") from e
    if not masters.ledgers:
        raise NoLedgersError("No ledgers found in the file")

    # one in-flight import per tenant (policy — see module docstring)
    session.execute(
        delete(StagingTallyParty).where(
            StagingTallyParty.tenant_id == tenant_id,
            StagingTallyParty.committed_as.is_(None),
        )
    )

    tree = GroupTree(masters.groups)

    grp_counts: dict[str, int] = {}
    grp_role: dict[str, PartyRole | None] = {}
    for led in masters.ledgers:
        top = (
            tree.top_group(led.parent)
            or (led.parent or "").strip().lower()
            or "(ungrouped)"
        )
        grp_counts[top] = grp_counts.get(top, 0) + 1
        grp_role.setdefault(top, tree.role_for(led.parent))

    gstins_in_file: dict[str, int] = {}
    for led in masters.ledgers:
        if led.gstin:
            try:
                g = validate_gstin(led.gstin)
                if g:
                    gstins_in_file[g] = gstins_in_file.get(g, 0) + 1
            except ValueError:
                pass

    to_stage: list[tuple[TallyLedger, PartyRole, bool]] = []
    for led in masters.ledgers:
        role = tree.role_for(led.parent)
        if role is None:
            continue
        anc = tree.roots_of(led.parent)
        dual = bool(anc & {"sundry debtors"}) and bool(anc & {"sundry creditors"})
        to_stage.append((led, role, dual))

    matches = match_ledgers_bulk(
        session, tenant_id, to_stage, gstins_in_file=gstins_in_file
    )

    staged = 0
    for (led, _role, _dual), mr in zip(to_stage, matches, strict=True):
        session.add(
            StagingTallyParty(
                tenant_id=tenant_id,
                batch_id=batch_id,
                sync_job_id=sync_job_id,
                tally_guid=led.guid,
                ledger_name=led.name,
                parent_group=led.parent,
                gstin=led.gstin,
                pan=led.pan,
                state_name=led.state_name,
                phone=led.phone,
                email=led.email,
                address_lines_json=led.address_lines or None,
                pincode=led.pincode,
                raw_xml=(led.raw_xml or "")[:_RAW_XML_KEEP] or None,
                proposed_role=mr.proposed_role,
                match_method=mr.method,
                match_party_id=mr.party_id,
                flags_json=mr.flags or None,
            )
        )
        staged += 1

    session.flush()

    groups = [
        PartyGroupCount(
            name=name, ledger_count=cnt, implied_role=grp_role.get(name)
        )
        for name, cnt in sorted(grp_counts.items(), key=lambda kv: -kv[1])
    ]
    # every ledger + group name, for the ledger-map dropdowns (connector only)
    known: list[dict] = [
        {"name": g.name, "parent": g.parent, "kind": "group"}
        for g in masters.groups
    ]
    known += [
        {"name": led.name, "parent": led.parent, "kind": "ledger"}
        for led in masters.ledgers
    ]

    return PartyStagingSummary(
        batch_id=batch_id, total=staged, groups=groups, known_ledgers=known
    )


# --------------------------------------------------------------------------
# stock items
# --------------------------------------------------------------------------


@dataclass
class StockGroupCount:
    name: str
    item_count: int


@dataclass
class ItemStagingSummary:
    batch_id: str
    total: int
    dummies_skipped: int
    groups: list[StockGroupCount] = field(default_factory=list)


def _clip(value: str | None, key: str) -> str | None:
    if not value:
        return None
    return value[: _PARSED_LIMITS[key]]


def _map_uom(base_units: str | None) -> str | None:
    return normalize_uom(base_units)[:20] or None


def _proposed_type(base_units: str | None) -> ItemType:
    return ItemType.mrp if is_mrp_uom(base_units) else ItemType.bulk


def stage_stock_items_xml(
    session: Session,
    tenant_id: str,
    raw: bytes,
    *,
    batch_id: str,
    seed_all_hsn: bool = False,
    sync_job_id: str | None = None,
) -> ItemStagingSummary:
    try:
        stock = parse_stock_items(raw)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Could not parse the Tally XML: {e}") from e
    if not stock.items:
        raise NoStockItemsError("No stock items found in the file")

    session.execute(
        delete(StagingTallyItem).where(
            StagingTallyItem.tenant_id == tenant_id,
            StagingTallyItem.committed_as.is_(None),
        )
    )

    synonyms = load_synonym_map(session, tenant_id)
    brands = [
        c.name
        for c in session.scalars(
            select(ItemCategory).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    ]

    guids_in_file: dict[str, int] = {}
    for si in stock.items:
        if si.guid:
            guids_in_file[si.guid] = guids_in_file.get(si.guid, 0) + 1

    kept = [si for si in stock.items if not is_zero_history_dummy(si)]
    dummies = len(stock.items) - len(kept)

    matches = match_stock_items_bulk(
        session, tenant_id, kept, guids_in_file=guids_in_file, synonyms=synonyms
    )

    grp_counts: dict[str, int] = {}
    staged = 0
    for si, mr in zip(kept, matches, strict=True):
        top = (si.parent or "(ungrouped)").strip() or "(ungrouped)"
        grp_counts[top] = grp_counts.get(top, 0) + 1

        p = parse_product_line(si.name, brands=brands, synonyms=synonyms)
        session.add(
            StagingTallyItem(
                tenant_id=tenant_id,
                batch_id=batch_id,
                sync_job_id=sync_job_id,
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
                parsed_metal=_clip(p.brand, "parsed_metal"),
                parsed_shape=_clip(p.product, "parsed_shape"),
                parsed_grade=None,
                parsed_size_text=_clip(p.size, "parsed_size_text"),
                parsed_sku=_clip(p.sku, "parsed_sku"),
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
    return ItemStagingSummary(
        batch_id=batch_id, total=staged, dummies_skipped=dummies, groups=groups
    )

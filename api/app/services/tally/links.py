"""Which Tally master is this record, in which Tally company? (S1.2)

A customer or item can be linked in each of a firm's Tally companies, under a different Tally id each
time. `tally_link` holds one row per (company, record). Every read of "is it linked, and to what" and
every write of a master's Tally id goes through this module.

Transition: `party.tally_guid` / `item.tally_guid` predate links. They are now read-only fallbacks, used
only for a record that has no link in ANY company yet (imported before links existed, or while the firm
had no company). Nothing writes them while the firm has a company: `record_master_guid` writes the link.
When a firm links its first company, `adopt_legacy_links` turns those leftovers into links.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, Party, TallyCompany, TallyLink, TallySyncJob
from app.services.tally.companies import get_default_company

MASTER_TYPES = ("party", "item")
_MODELS = {"party": Party, "item": Item}


def company_for_import(session: Session, tenant_id: str, rows) -> TallyCompany | None:  # type: ignore[no-untyped-def]
    """The company a staged batch belongs to: the one its pull job ran for, else the firm's default.
    None when the firm has no company yet (an import by file)."""
    for row in rows:
        if getattr(row, "company_id", None):  # the batch says which company it was pulled from (S1.6)
            company = session.get(TallyCompany, row.company_id)
            if company is not None and company.tenant_id == tenant_id:
                return company
        if row.sync_job_id:
            job = session.get(TallySyncJob, row.sync_job_id)
            company = session.get(TallyCompany, job.company_id) if job is not None else None
            if company is not None and company.tenant_id == tenant_id:
                return company
    return get_default_company(session, tenant_id)


def get_link(
    session: Session, company_id: str, entity_type: str, entity_id: str
) -> TallyLink | None:
    return session.scalar(
        select(TallyLink).where(
            TallyLink.company_id == company_id,
            TallyLink.entity_type == entity_type,
            TallyLink.entity_id == entity_id,
        )
    )


def _has_any_link(session: Session, entity_type: str, entity_id: str) -> bool:
    return (
        session.scalar(
            select(TallyLink.id)
            .where(TallyLink.entity_type == entity_type, TallyLink.entity_id == entity_id)
            .limit(1)
        )
        is not None
    )


def guid_for(
    session: Session, company: TallyCompany, entity_type: str, entity: Party | Item | None
) -> str | None:
    """The record's Tally id in `company`, or None when it is not linked there."""
    if entity is None:
        return None
    link = get_link(session, company.id, entity_type, entity.id)
    if link is not None:
        return link.tally_guid
    legacy = entity.tally_guid
    if legacy and company.is_default and not _has_any_link(session, entity_type, entity.id):
        return legacy  # transition fallback: the default company, and no link in any company yet
    return None


def unit_for(session: Session, company: TallyCompany, item: Item | None) -> str | None:
    """The unit `company` holds for this item, in Tally's spelling; None when never seen there. A
    linked item reads its own link, so one company's unit is never taken for another's. Only an item
    with no link in any company falls back to the old per-item column (default company)."""
    if item is None:
        return None
    link = get_link(session, company.id, "item", item.id)
    if link is not None:
        return link.tally_unit
    if item.tally_unit and company.is_default and not _has_any_link(session, "item", item.id):
        return item.tally_unit
    return None


def set_unit(
    session: Session, company: TallyCompany | None, item: Item, unit: str | None
) -> None:
    """Remember the unit Tally holds for the item in `company` (blank is ignored). With no company, or
    no link yet, it is kept on the item until `adopt_legacy_links` moves it onto a link."""
    unit = (unit or "").strip()[:40]
    if not unit:
        return
    link = get_link(session, company.id, "item", item.id) if company is not None else None
    if link is not None:
        link.tally_unit = unit
    elif company is None or company.is_default:
        item.tally_unit = unit


def is_linked(
    session: Session, company: TallyCompany, entity_type: str, entity: Party | Item | None
) -> bool:
    return guid_for(session, company, entity_type, entity) is not None


def linked_ids(session: Session, entity_type: str, ids: list[str]) -> set[str]:
    """Which of these records are linked in ANY company (links, or the legacy fallback)."""
    if not ids:
        return set()
    found = set(
        session.scalars(
            select(TallyLink.entity_id).where(
                TallyLink.entity_type == entity_type, TallyLink.entity_id.in_(ids)
            )
        )
    )
    model = _MODELS[entity_type]
    found |= set(
        session.scalars(
            select(model.id).where(model.id.in_(ids), model.tally_guid.is_not(None))
        )
    )
    return found


def _carried_unit(company: TallyCompany, entity_type: str, entity: Party | Item) -> str | None:
    """A unit kept on the item before it had a link moves to the default company's first link."""
    if entity_type == "item" and company.is_default:
        return entity.tally_unit  # type: ignore[union-attr]
    return None


def _our_name(entity_type: str, entity: Party | Item) -> str | None:
    """The name we hold for the record, used when no Tally-side name was seen: a first push names the
    master by ours, and from then on the link remembers it."""
    return (entity.legal_name if entity_type == "party" else entity.name) or None  # type: ignore[union-attr]


def tally_name_for(
    session: Session, company: TallyCompany, entity_type: str, entity: Party | Item
) -> str:
    """The name to send Tally for this record in `company`: the link's own once linked (so a rename
    here does not break the push), else ours."""
    link = get_link(session, company.id, entity_type, entity.id)
    if link is not None and link.tally_name:
        return link.tally_name
    return _our_name(entity_type, entity) or ""


def name_differs(
    session: Session, company: TallyCompany, entity_type: str, entity: Party | Item
) -> bool:
    """Linked, and our name is no longer the one Tally holds."""
    link = get_link(session, company.id, entity_type, entity.id)
    ours = _our_name(entity_type, entity) or ""
    return (
        link is not None
        and bool(link.tally_name)
        and " ".join(link.tally_name.lower().split()) != " ".join(ours.lower().split())
    )


def record_master_guid(
    session: Session,
    tenant_id: str,
    company: TallyCompany | None,
    entity_type: str,
    entity: Party | Item,
    guid: str | None,
    *,
    masterid: str | None = None,
    unit: str | None = None,
    name: str | None = None,
) -> TallyLink | None:
    """Remember that `entity` is Tally master `guid` in `company`. The one place a master's Tally id is
    written. Never moves a guid another record already holds in that company (Tally's id names exactly
    one master), and never overwrites an existing link of this record.

    With no company (an import by file, before the firm linked one) the id is kept on the legacy column
    until `adopt_legacy_links` turns it into a link.
    """
    guid = (guid or "").strip()
    if not guid:
        return None
    unit = (unit or "").strip()[:40] or None
    if company is None:
        if not entity.tally_guid:
            entity.tally_guid = guid
        if unit and entity_type == "item":
            entity.tally_unit = unit
        return None
    existing = get_link(session, company.id, entity_type, entity.id)
    if existing is not None:
        if unit and entity_type == "item":
            existing.tally_unit = unit  # Tally owns the unit name: a pull refreshes it
        if name and name.strip():
            existing.tally_name = name.strip()[:500]  # and its own name for the master
        return existing
    held = session.scalar(
        select(TallyLink.id).where(
            TallyLink.company_id == company.id,
            TallyLink.entity_type == entity_type,
            TallyLink.tally_guid == guid,
        )
    )
    if held is not None:
        return None
    link = TallyLink(
        tenant_id=tenant_id,
        company_id=company.id,
        entity_type=entity_type,
        entity_id=entity.id,
        tally_guid=guid,
        tally_masterid=masterid,
        tally_unit=unit or _carried_unit(company, entity_type, entity),
        tally_name=(name or "").strip()[:500] or _our_name(entity_type, entity),
        last_pulled_at=datetime.now(UTC),
    )
    session.add(link)
    session.flush()
    return link


def adopt_legacy_links(session: Session, company: TallyCompany) -> int:
    """Turn legacy `tally_guid` leftovers (records with no link anywhere) into links in `company`.
    Run when a firm links its first Tally company. Returns how many links were made."""
    made = 0
    for entity_type, model in _MODELS.items():
        rows = session.scalars(
            select(model)
            .where(model.tenant_id == company.tenant_id, model.tally_guid.is_not(None))
            .order_by(model.created_at)
        ).all()
        for entity in rows:
            if _has_any_link(session, entity_type, entity.id):
                continue
            if record_master_guid(session, company.tenant_id, company, entity_type, entity,
                                  entity.tally_guid) is not None:
                made += 1
    return made

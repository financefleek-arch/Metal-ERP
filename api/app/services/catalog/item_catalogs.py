"""Customer catalogs built from items.

A customer catalog offers what the firm has, so it is built from the item list, never straight
from a supplier price list: the price is the item's selling rate, the photo is the item's photo,
the pack size is the item's. Only items that are In stock go in (Expected ones on request, with
an optional "on order" label). Each build is a snapshot; a rebuild repeats the stored selection
(a filter is evaluated again) as the next version of the same series.

Nothing here reads a cost price or a supplier code.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    CatalogOutputJob,
    CustomerCatalog,
    Item,
    ItemCategory,
    MediaAsset,
    Tenant,
)
from app.models._mixins import Availability
from app.schemas_customer_catalog import CatalogLayout, CatalogSelection
from app.services import media as media_svc
from app.services.catalog.catalog_pdf import Brand, CatalogEntry, CatalogPdfOptions, render_pdf
from app.services.catalog.storage import CatalogStorage
from app.services.item_filter import resolve_ids

log = logging.getLogger("catalog.items")

# Up to this many items are built inside the request; more run as a background job.
SYNC_ITEM_LIMIT = 100
# One customer catalog never exceeds this many items.
MAX_ITEMS = 5000
_CHUNK = 500
ON_ORDER = "On order"


@dataclass
class Plan:
    selected: int = 0
    items: list[Item] = field(default_factory=list)  # the ones that will be printed, in order
    left_out: dict[str, int] = field(
        default_factory=lambda: {"out_of_stock": 0, "discontinued": 0, "expected": 0}
    )
    no_price: int = 0
    missing: list[Item] = field(default_factory=list)  # printed items with no photo


def pdf_key(tenant_id: str, series_id: str, catalog_id: str) -> str:
    return f"customer-catalogs/{tenant_id}/{series_id}/{catalog_id}.pdf"


def layout_options(layout: CatalogLayout) -> CatalogPdfOptions:
    return CatalogPdfOptions(
        columns=layout.columns,
        group_by=layout.group_by,
        show_code=layout.show_code,
        price_basis=layout.price_basis,
        contents=layout.contents,
    )


def layout_to_json(layout: CatalogLayout) -> dict[str, object]:
    return layout.model_dump()


def layout_from_json(raw: dict[str, object] | None) -> CatalogLayout:
    return CatalogLayout.model_validate(raw or {})


def selection_to_json(sel: CatalogSelection) -> dict[str, object]:
    return sel.model_dump(mode="json", exclude_none=True)


def selection_from_json(raw: dict[str, object] | None) -> CatalogSelection:
    return CatalogSelection.model_validate(raw or {})


def selection_kind(raw: dict[str, object] | None) -> str:
    return "filter" if raw and raw.get("filter") is not None else "ids"


def brand_for(tenant: Tenant, title: str) -> Brand:
    """Cover details from the firm profile."""
    address_parts = [
        (tenant.address or "").splitlines()[0] if tenant.address else "",
        tenant.city or "",
        tenant.pincode or "",
    ]
    return Brand(
        firm_name=tenant.trade_name or tenant.legal_name,
        title=title,
        phone=tenant.phone,
        email=tenant.email,
        address=", ".join(p.strip() for p in address_parts if p and p.strip()) or None,
    )


def today() -> date:
    return datetime.now(UTC).date()


def _load_items(session: Session, tenant_id: str, ids: list[str]) -> dict[str, Item]:
    found: dict[str, Item] = {}
    for i in range(0, len(ids), _CHUNK):
        for it in session.scalars(
            select(Item).where(Item.tenant_id == tenant_id, Item.id.in_(ids[i : i + _CHUNK]))
        ):
            found[it.id] = it
    return found


def _category_names(session: Session, tenant_id: str) -> dict[str, str]:
    return dict(
        session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    )


def plan(session: Session, tenant_id: str, ids: list[str], layout: CatalogLayout) -> Plan:
    """What would be printed from `ids`, and what is left out and why."""
    p = Plan(selected=len(ids))
    items = _load_items(session, tenant_id, ids)
    cats = _category_names(session, tenant_id)
    allowed = {Availability.in_stock.value}
    if layout.include_expected:
        allowed.add(Availability.expected.value)

    keep: list[Item] = []
    for item_id in ids:
        it = items.get(item_id)
        if it is None or it.merged_into_id is not None or it.status == "archived":
            continue
        if it.availability not in allowed:
            if it.availability in p.left_out:
                p.left_out[it.availability] += 1
            continue
        if it.default_rate is None or Decimal(str(it.default_rate)) <= 0:
            p.no_price += 1
            continue
        keep.append(it)

    keep.sort(
        key=lambda it: (
            cats.get(it.category_id or "", "").casefold() or "￿",
            it.name.casefold(),
            it.id,
        )
    )
    p.items = keep
    p.missing = [it for it in keep if it.primary_media_id is None]
    return p


def resolve_selection(
    session: Session, tenant_id: str, selection: CatalogSelection
) -> list[str]:
    return resolve_ids(session, tenant_id, selection.ids, selection.filter)


def load_entries(
    session: Session,
    storage: CatalogStorage,
    tenant_id: str,
    items: list[Item],
    layout: CatalogLayout,
) -> list[CatalogEntry]:
    """Printable entries, in `items` order. Only our price and our code leave this function."""
    cats = _category_names(session, tenant_id)
    media_ids = [it.primary_media_id for it in items if it.primary_media_id]
    assets: list[MediaAsset] = []
    for i in range(0, len(media_ids), _CHUNK):
        assets += list(
            session.scalars(select(MediaAsset).where(MediaAsset.id.in_(media_ids[i : i + _CHUNK])))
        )
    jpegs = media_svc.print_jpegs(storage, assets)
    out: list[CatalogEntry] = []
    for it in items:
        note = (
            ON_ORDER
            if layout.label_expected and it.availability == Availability.expected.value
            else None
        )
        out.append(
            CatalogEntry(
                code=(it.sku or it.barcode or ""),
                name=it.name,
                group=cats.get(it.category_id) if it.category_id else None,
                price=Decimal(str(it.default_rate)),
                pack_qty=int(it.pack_qty or 1),
                image=jpegs.get(it.primary_media_id) if it.primary_media_id else None,
                note=note,
            )
        )
    return out


def next_version(session: Session, tenant_id: str, series_id: str) -> int:
    top = session.scalar(
        select(func.max(CustomerCatalog.version)).where(
            CustomerCatalog.tenant_id == tenant_id, CustomerCatalog.series_id == series_id
        )
    )
    return (top or 0) + 1


def store(
    session: Session,
    storage: CatalogStorage,
    *,
    tenant_id: str,
    user_id: str | None,
    series_id: str,
    title: str,
    layout: CatalogLayout,
    selection: CatalogSelection,
    member_ids: list[str],
    pdf: bytes,
    page_count: int,
) -> CustomerCatalog:
    """Save the PDF and its row as the next version of the series. Retries once if two builds
    race for the same version number."""
    new_id = str(uuid.uuid4())
    key = pdf_key(tenant_id, series_id, new_id)
    storage.put(key, pdf, "application/pdf")
    for attempt in (1, 2):
        row = CustomerCatalog(
            id=new_id,
            tenant_id=tenant_id,
            series_id=series_id,
            created_by=user_id,
            version=next_version(session, tenant_id, series_id),
            title=title,
            options_json=layout_to_json(layout),
            selection_json=selection_to_json(selection),
            member_ids_json=member_ids,
            item_count=len(member_ids),
            page_count=page_count,
            byte_size=len(pdf),
            pdf_key=key,
        )
        try:
            with session.begin_nested():
                session.add(row)
            return row
        except IntegrityError:
            if attempt == 2:
                storage.delete(key)
                raise
            session.expire_all()
    raise AssertionError("unreachable")


def build(
    session: Session,
    storage: CatalogStorage,
    tenant: Tenant,
    *,
    user_id: str | None,
    series_id: str,
    title: str,
    layout: CatalogLayout,
    selection: CatalogSelection,
    item_ids: list[str],
    progress=None,  # type: ignore[no-untyped-def]
) -> CustomerCatalog:
    """Build and store one version from exactly `item_ids` (already planned)."""
    items_by_id = _load_items(session, tenant.id, item_ids)
    items = [items_by_id[i] for i in item_ids if i in items_by_id]
    entries = load_entries(session, storage, tenant.id, items, layout)
    pdf, pages = render_pdf(
        entries, layout_options(layout), brand_for(tenant, title), today(), progress
    )
    return store(
        session,
        storage,
        tenant_id=tenant.id,
        user_id=user_id,
        series_id=series_id,
        title=title,
        layout=layout,
        selection=selection,
        member_ids=[it.id for it in items],
        pdf=pdf,
        page_count=pages,
    )


def create_job(
    session: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    series_id: str,
    title: str,
    layout: CatalogLayout,
    selection: CatalogSelection,
    item_ids: list[str],
) -> CatalogOutputJob:
    job = CatalogOutputJob(
        tenant_id=tenant_id,
        catalog_id=None,
        created_by=user_id,
        kind="customer_catalog",
        params_json={
            "series_id": series_id,
            "title": title,
            "layout": layout_to_json(layout),
            "selection": selection_to_json(selection),
            "item_ids": item_ids,
        },
        status="queued",
        progress=0,
        total=len(item_ids),
    )
    session.add(job)
    session.flush()
    return job


def run_job(job_id: str, storage: CatalogStorage) -> None:
    """Build the catalog for `job_id`. Runs as a background task; reports on the job row and never
    raises."""
    with SessionLocal() as s:
        job = s.get(CatalogOutputJob, job_id)
        if job is None or job.params_json is None:
            return
        job.status = "running"
        s.commit()
        try:
            params = job.params_json
            tenant = s.get(Tenant, job.tenant_id)
            if tenant is None:
                raise RuntimeError("the firm no longer exists")

            def progress(done: int, total: int) -> None:
                job.progress = done
                s.commit()

            row = build(
                s,
                storage,
                tenant,
                user_id=job.created_by,
                series_id=str(params["series_id"]),
                title=str(params["title"]),
                layout=layout_from_json(params["layout"]),
                selection=selection_from_json(params["selection"]),
                item_ids=list(params["item_ids"]),
                progress=progress,
            )
            job.params_json = {**params, "customer_catalog_id": row.id}
            job.page_count = row.page_count
            job.progress = job.total
            job.status = "done"
            s.commit()
        except Exception as exc:  # noqa: BLE001 - report on the row, never crash the worker
            log.exception("customer catalog job %s failed", job_id)
            s.rollback()
            failed = s.get(CatalogOutputJob, job_id)
            if failed is not None:
                failed.status = "error"
                failed.error = f"{type(exc).__name__}: {exc}"[:500]
                s.commit()


# --------------------------------------------------------------------------- out of date


def is_stale(session: Session, cc: CustomerCatalog) -> bool:
    """Out of date when a printed item changed or went away after the build, or when a filter
    now matches different items. Checked on read, so nothing has to remember to flag it."""
    members = list(cc.member_ids_json or [])
    since = cc.created_at
    present = 0
    for i in range(0, len(members), _CHUNK):
        chunk = members[i : i + _CHUNK]
        rows = list(
            session.execute(
                select(Item.id, Item.updated_at, Item.status, Item.merged_into_id).where(
                    Item.tenant_id == cc.tenant_id, Item.id.in_(chunk)
                )
            )
        )
        for _id, updated, status, merged in rows:
            if status == "archived" or merged is not None:
                return True
            if updated is not None and _utc(updated) > _utc(since):
                return True
            present += 1
    if present < len(members):
        return True  # an item was deleted
    sel = selection_from_json(cc.selection_json)
    if sel.filter is not None:
        try:
            ids = resolve_selection(session, cc.tenant_id, sel)
        except HTTPException:
            return True
        now = plan(session, cc.tenant_id, ids, layout_from_json(cc.options_json))
        if {it.id for it in now.items} != set(members):
            return True
    return False


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

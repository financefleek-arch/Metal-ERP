"""Barcode labels printed from items.

A label carries the item's own code (its SKU, or barcode when it has no SKU), name and, when asked,
its selling rate. The selection is an Items selection (ticked ids or everything matching a filter).
Items that are archived or merged away are left out and counted. An item without a code gets the
next firm-wide number when the labels are made (or is left out when the caller asks for that), so a
hand-added item can be labelled straight away. Labels are not tied to availability: stock arrives,
then gets labelled.

Small prints come back as the PDF itself; larger ones run as a background job (`kind="item_labels"`,
no supplier catalog) and are polled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CatalogOutputJob, Item, Tenant
from app.schemas_customer_catalog import CatalogSelection
from app.services.catalog import output_jobs
from app.services.catalog.codes import allocate_codes
from app.services.catalog.labels import LabelItem, LabelOptions, render_pdf
from app.services.catalog.output_jobs import options_to_json
from app.services.catalog.storage import CatalogStorage
from app.services.item_filter import resolve_ids

log = logging.getLogger("catalog.item_labels")
_CHUNK = 500


@dataclass
class LabelPlan:
    selected: int = 0
    items: list[Item] = field(default_factory=list)
    no_code: int = 0  # printed items that have no code yet (they get one when printed)
    inactive: int = 0  # archived or merged away
    no_price: int = 0  # printed, but no selling rate to show


def resolve_selection(session: Session, tenant_id: str, selection: CatalogSelection) -> list[str]:
    return resolve_ids(session, tenant_id, selection.ids, selection.filter)


def _load(session: Session, tenant_id: str, ids: list[str]) -> dict[str, Item]:
    found: dict[str, Item] = {}
    for i in range(0, len(ids), _CHUNK):
        for it in session.scalars(
            select(Item).where(Item.tenant_id == tenant_id, Item.id.in_(ids[i : i + _CHUNK]))
        ):
            found[it.id] = it
    return found


def code_of(item: Item) -> str:
    return (item.sku or item.barcode or "").strip()


def _price(item: Item) -> Decimal | None:
    if item.default_rate is None:
        return None
    rate = Decimal(str(item.default_rate))
    return rate if rate > 0 else None


def plan(session: Session, tenant_id: str, ids: list[str]) -> LabelPlan:
    p = LabelPlan(selected=len(ids))
    found = _load(session, tenant_id, ids)
    for item_id in ids:
        it = found.get(item_id)
        if it is None or it.merged_into_id is not None or it.status == "archived":
            p.inactive += 1
            continue
        if not code_of(it):
            p.no_code += 1
        if _price(it) is None:
            p.no_price += 1
        p.items.append(it)
    p.items.sort(key=lambda it: (it.name.casefold(), it.id))
    return p


def assign_missing_codes(session: Session, tenant: Tenant, items: list[Item]) -> int:
    """Give each item without a code the next firm-wide number. Caller commits."""
    missing = [it for it in items if not code_of(it)]
    for it, code in zip(missing, allocate_codes(session, tenant, len(missing)), strict=True):
        it.sku = code
    session.flush()
    return len(missing)


def drop_uncoded(items: list[Item]) -> list[Item]:
    return [it for it in items if code_of(it)]


def label_items(items: list[Item]) -> list[LabelItem]:
    return [LabelItem(code=code_of(it), name=it.name, price=_price(it)) for it in items]


def result_key(tenant_id: str, job_id: str) -> str:
    return f"catalog/{tenant_id}/item-labels/{job_id}.pdf"


def create_job(
    session: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    item_ids: list[str],
    preset: str,
    opts: LabelOptions,
    pages: int,
) -> CatalogOutputJob:
    job = CatalogOutputJob(
        tenant_id=tenant_id,
        catalog_id=None,
        created_by=user_id,
        kind="item_labels",
        params_json={"item_ids": item_ids, "preset": preset, "options": options_to_json(opts)},
        status="queued",
        progress=0,
        total=len(item_ids) * opts.copies,
        page_count=pages,
    )
    session.add(job)
    session.flush()
    return job


def run_job(job_id: str, storage: CatalogStorage) -> None:
    """Build the PDF for `job_id`. Background task: reports on the row and never raises."""
    with SessionLocal() as s:
        job = s.get(CatalogOutputJob, job_id)
        if job is None or job.params_json is None:
            return
        job.status = "running"
        s.commit()
        try:
            params = job.params_json
            raw = params["options"]
            opts = LabelOptions(
                show_name=bool(raw["show_name"]),
                show_code=bool(raw["show_code"]),
                show_price=bool(raw["show_price"]),
                copies=int(raw["copies"]),
                start_at=int(raw["start_at"]),
            )
            ids = list(params["item_ids"])
            found = _load(s, job.tenant_id, ids)
            items = label_items([found[i] for i in ids if i in found])

            def progress(done: int, total: int) -> None:
                job.progress = done
                s.commit()

            pdf, scannable = render_pdf(
                items, str(params["preset"]), opts, progress, title="Labels"
            )
            key = result_key(job.tenant_id, job.id)
            storage.put(key, pdf, "application/pdf")
            job.result_key = key
            job.scan_warning = not scannable
            job.status = "done"
            job.progress = job.total
            s.commit()
        except Exception as exc:  # noqa: BLE001 - report on the row, never crash the worker
            log.exception("item label job %s failed", job_id)
            s.rollback()
            failed = s.get(CatalogOutputJob, job_id)
            if failed is not None:
                failed.status = "error"
                failed.error = f"{type(exc).__name__}: {exc}"[:500]
                s.commit()


# re-exported so the router has one import for sizes
MAX_LABELS = output_jobs.MAX_LABELS
SYNC_LABEL_LIMIT = output_jobs.SYNC_LABEL_LIMIT
pages_for = output_jobs.pages_for

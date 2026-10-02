"""Background output jobs: label PDFs too big to build inside one request.

The request creates a `catalog_output_job` row (and commits it) before scheduling
`run_labels_job` as a FastAPI background task, which opens its own session. There is no
worker fleet: if the process restarts mid-job the row is left `running`, and the next
poll marks it failed once it has not moved for `STALE_AFTER`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CatalogOutputJob, SupplierCatalog, SupplierCatalogItem
from app.services.catalog.labels import LabelItem, LabelOptions, page_count, render_pdf
from app.services.catalog.storage import CatalogStorage

log = logging.getLogger("catalog.jobs")

# Up to this many labels are built inside the request; more run as a background job.
SYNC_LABEL_LIMIT = 200
# One print run never exceeds this (a roll of 5,000 labels is already a lot of paper).
MAX_LABELS = 5000
STALE_AFTER = timedelta(minutes=20)
RETENTION = timedelta(days=7)
_CHUNK = 500


def result_key(tenant_id: str, catalog_id: str, job_id: str) -> str:
    return f"catalog/{tenant_id}/{catalog_id}/out/{job_id}.pdf"


def load_label_items(
    session: Session, tenant_id: str, catalog_id: str, item_ids: list[str]
) -> list[LabelItem]:
    """Label data for `item_ids`, in that order."""
    C = SupplierCatalogItem
    found: dict[str, LabelItem] = {}
    for i in range(0, len(item_ids), _CHUNK):
        chunk = item_ids[i : i + _CHUNK]
        for item_id, code, name, price in session.execute(
            select(C.id, C.code, C.display_name, C.sell_price).where(
                C.tenant_id == tenant_id, C.catalog_id == catalog_id, C.id.in_(chunk)
            )
        ):
            found[item_id] = LabelItem(code=code, name=name, price=Decimal(price))
    return [found[i] for i in item_ids if i in found]


def options_to_json(opts: LabelOptions) -> dict[str, object]:
    return {
        "show_name": opts.show_name,
        "show_code": opts.show_code,
        "show_price": opts.show_price,
        "copies": opts.copies,
        "start_at": opts.start_at,
    }


def create_labels_job(
    session: Session,
    *,
    catalog: SupplierCatalog,
    user_id: str | None,
    item_ids: list[str],
    preset: str,
    opts: LabelOptions,
    pages: int,
) -> CatalogOutputJob:
    job = CatalogOutputJob(
        tenant_id=catalog.tenant_id,
        catalog_id=catalog.id,
        created_by=user_id,
        kind="labels",
        params_json={"item_ids": item_ids, "preset": preset, "options": options_to_json(opts)},
        status="queued",
        progress=0,
        total=len(item_ids) * opts.copies,
        page_count=pages,
    )
    session.add(job)
    session.flush()
    return job


def run_labels_job(job_id: str, storage: CatalogStorage) -> None:
    """Build the PDF for `job_id` and store it. Runs in a background task."""
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
            items = load_label_items(s, job.tenant_id, job.catalog_id, list(params["item_ids"]))
            title = s.get(SupplierCatalog, job.catalog_id)

            def progress(done: int, total: int) -> None:
                job.progress = done
                s.commit()

            heading = f"Labels {title.title}" if title else "Labels"
            pdf, scannable = render_pdf(items, str(params["preset"]), opts, progress, title=heading)
            key = result_key(job.tenant_id, job.catalog_id, job.id)
            storage.put(key, pdf, "application/pdf")
            job.result_key = key
            job.scan_warning = not scannable
            job.status = "done"
            job.progress = job.total
            s.commit()
        except Exception as exc:  # noqa: BLE001 - report on the row, never crash the worker
            log.exception("label job %s failed", job_id)
            s.rollback()
            failed = s.get(CatalogOutputJob, job_id)
            if failed is not None:
                failed.status = "error"
                failed.error = f"{type(exc).__name__}: {exc}"[:500]
                s.commit()


def refresh_status(session: Session, job: CatalogOutputJob) -> CatalogOutputJob:
    """Mark a job that has not moved for `STALE_AFTER` as failed (the process that was
    running it is gone)."""
    if job.status in ("queued", "running"):
        updated = job.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if datetime.now(UTC) - updated > STALE_AFTER:
            job.status = "error"
            job.error = "The print job was interrupted. Start it again."
            session.flush()
    return job


def prune_old_jobs(session: Session, catalog: SupplierCatalog, storage: CatalogStorage) -> int:
    """Delete this catalog's finished jobs (and files) older than `RETENTION`."""
    cutoff = datetime.now(UTC) - RETENTION
    old = list(
        session.scalars(
            select(CatalogOutputJob).where(
                CatalogOutputJob.catalog_id == catalog.id,
                CatalogOutputJob.status.in_(("done", "error")),
                CatalogOutputJob.updated_at < cutoff,
            )
        )
    )
    keys = [j.result_key for j in old if j.result_key]
    for j in old:
        session.delete(j)
    session.flush()
    try:
        storage.delete_many(keys)
    except Exception:  # noqa: BLE001 - leftovers are harmless
        log.warning("could not delete %d old output files", len(keys))
    return len(old)


def pages_for(n_items: int, preset_key: str, opts: LabelOptions) -> int:
    from app.services.catalog.labels import PRESETS

    return page_count(n_items * opts.copies, PRESETS[preset_key], opts)

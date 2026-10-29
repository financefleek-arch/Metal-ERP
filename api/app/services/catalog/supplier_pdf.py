"""A customer PDF straight out of a supplier price list: PDF in, PDF out, no items needed.

The price list already holds everything a customer catalog prints: our selling price, our code, our
group, the supplier's photo and the pack size. So this builds the same PDF as `item_catalogs`, from
the price-list rows, and never reads a cost price or a supplier code.

Nothing is hidden: every chosen row with a price is printed (a price list says nothing about stock).

A download is not rendered again when nothing changed. Each build is a `catalog_output_job` row that
remembers a fingerprint of everything that shows on the page (rows, layout, title, cover, day); the
same fingerprint is answered with the stored PDF, and any edit to a row, margin, group or photo
changes the fingerprint so the next download renders afresh. It is a cache, not a list of versions;
old files go with `output_jobs.prune_old_jobs`.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    CatalogOutputJob,
    ItemCategory,
    SupplierCatalog,
    SupplierCatalogItem,
    Tenant,
)
from app.schemas_customer_catalog import CatalogLayout
from app.services.catalog import item_catalogs
from app.services.catalog.catalog_pdf import CatalogEntry, render_pdf
from app.services.catalog.output_jobs import STALE_AFTER, result_key
from app.services.catalog.storage import CatalogStorage
from app.services.media import PRINT_EDGE, PRINT_QUALITY

log = logging.getLogger("catalog.supplier_pdf")

KIND = "price_list_pdf"
# Up to this many products are built inside the request; more run as a background job.
SYNC_LIMIT = 100
MAX_ROWS = 5000
_CHUNK = 500
# how many finished jobs are looked at when asking "was this exact PDF already built?"
_RECENT = 25


@dataclass
class Plan:
    selected: int = 0
    rows: list[SupplierCatalogItem] = field(default_factory=list)  # the ones that will be printed
    no_price: int = 0
    missing_photos: int = 0


def plan(rows: list[SupplierCatalogItem], show_prices: bool = True) -> Plan:
    p = Plan(selected=len(rows))
    for r in rows:
        if show_prices and (r.sell_price is None or Decimal(str(r.sell_price)) <= 0):
            p.no_price += 1
            continue
        p.rows.append(r)
    p.missing_photos = sum(1 for r in p.rows if not r.image_key)
    return p


def _category_names(session: Session, tenant_id: str) -> dict[str, str]:
    return {
        k: v
        for k, v in session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        ).all()
    }


def _row_facts(r: SupplierCatalogItem, cats: dict[str, str]) -> list[object]:
    """Everything about one row that shows on the page. No cost price, no supplier code."""
    return [
        r.id,
        r.code,
        r.display_name,
        cats.get(r.category_id or ""),
        f"{Decimal(str(r.sell_price)):.2f}" if r.sell_price is not None else None,
        int(r.pack_qty or 1),
        r.image_sha256 or r.image_key,
    ]


def fingerprint(
    session: Session,
    tenant: Tenant,
    rows: list[SupplierCatalogItem],
    layout: CatalogLayout,
    title: str,
) -> str:
    cats = _category_names(session, tenant.id)
    brand = item_catalogs.brand_for(tenant, title)
    payload = {
        "rows": [_row_facts(r, cats) for r in rows],
        "layout": item_catalogs.layout_to_json(layout),
        "brand": [brand.firm_name, brand.title, brand.phone, brand.email, brand.address],
        "day": item_catalogs.today().isoformat(),  # the cover carries the date
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _print_jpeg(data: bytes) -> bytes | None:
    """A JPEG sized for printing (a PDF cannot embed WebP); None for a crop that cannot be read."""
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((PRINT_EDGE, PRINT_EDGE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=PRINT_QUALITY)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - an unreadable crop is a placeholder, not a failure
        return None


def load_entries(
    session: Session, storage: CatalogStorage, tenant_id: str, rows: list[SupplierCatalogItem]
) -> list[CatalogEntry]:
    cats = _category_names(session, tenant_id)
    keys = [r.image_key for r in rows if r.image_key]
    blobs: dict[str, bytes | None] = {}
    for i in range(0, len(keys), _CHUNK):
        blobs.update(storage.get_many(keys[i : i + _CHUNK]))
    out: list[CatalogEntry] = []
    for r in rows:
        raw = blobs.get(r.image_key) if r.image_key else None
        out.append(
            CatalogEntry(
                code=r.code,
                name=r.display_name,
                group=cats.get(r.category_id or ""),
                price=Decimal(str(r.sell_price if r.sell_price is not None else 0)),
                pack_qty=int(r.pack_qty or 1),
                image=_print_jpeg(raw) if raw else None,
            )
        )
    return out


def find_built(session: Session, catalog_id: str, fp: str) -> CatalogOutputJob | None:
    """A finished job that already holds this exact PDF."""
    recent = session.scalars(
        select(CatalogOutputJob)
        .where(
            CatalogOutputJob.catalog_id == catalog_id,
            CatalogOutputJob.kind == KIND,
            CatalogOutputJob.status == "done",
            CatalogOutputJob.result_key.is_not(None),
        )
        .order_by(CatalogOutputJob.updated_at.desc())
        .limit(_RECENT)
    )
    for job in recent:
        if (job.params_json or {}).get("fingerprint") == fp:
            return job
    return None


def find_in_flight(session: Session, catalog_id: str, fp: str) -> CatalogOutputJob | None:
    """A build of this exact PDF that is queued or running now, so a retry or a second click joins it
    instead of starting another (they would share the CPU and each take several times longer). A job
    that has not moved for `STALE_AFTER` is treated as dead and ignored."""
    cutoff = datetime.now(UTC) - STALE_AFTER
    recent = session.scalars(
        select(CatalogOutputJob)
        .where(
            CatalogOutputJob.catalog_id == catalog_id,
            CatalogOutputJob.kind == KIND,
            CatalogOutputJob.status.in_(("queued", "running")),
            CatalogOutputJob.updated_at > cutoff,
        )
        .order_by(CatalogOutputJob.created_at.desc())
        .limit(_RECENT)
    )
    for job in recent:
        if (job.params_json or {}).get("fingerprint") == fp:
            return job
    return None


def create_job(
    session: Session,
    *,
    catalog: SupplierCatalog,
    user_id: str | None,
    title: str,
    layout: CatalogLayout,
    row_ids: list[str],
    fp: str,
) -> CatalogOutputJob:
    job = CatalogOutputJob(
        tenant_id=catalog.tenant_id,
        catalog_id=catalog.id,
        created_by=user_id,
        kind=KIND,
        params_json={
            "fingerprint": fp,
            "title": title,
            "layout": item_catalogs.layout_to_json(layout),
            "row_ids": row_ids,
        },
        status="queued",
        progress=0,
        total=len(row_ids),
    )
    session.add(job)
    session.flush()
    return job


def run_job(job_id: str, storage: CatalogStorage) -> None:
    """Build the PDF for `job_id` and store it. Never raises; reports on the job row."""
    with SessionLocal() as s:
        job = s.get(CatalogOutputJob, job_id)
        if job is None or job.params_json is None or job.catalog_id is None:
            return
        job.status = "running"
        s.commit()
        try:
            params = job.params_json
            tenant = s.get(Tenant, job.tenant_id)
            if tenant is None:
                raise RuntimeError("the firm no longer exists")
            ids = list(params["row_ids"])
            found: dict[str, SupplierCatalogItem] = {}
            for i in range(0, len(ids), _CHUNK):
                for r in s.scalars(
                    select(SupplierCatalogItem).where(
                        SupplierCatalogItem.tenant_id == job.tenant_id,
                        SupplierCatalogItem.catalog_id == job.catalog_id,
                        SupplierCatalogItem.id.in_(ids[i : i + _CHUNK]),
                    )
                ):
                    found[r.id] = r
            rows = [found[i] for i in ids if i in found]
            layout = item_catalogs.layout_from_json(params["layout"])
            entries = load_entries(s, storage, job.tenant_id, rows)

            def progress(done: int, total: int) -> None:
                job.progress = done
                s.commit()

            pdf, pages = render_pdf(
                entries,
                item_catalogs.layout_options(layout),
                item_catalogs.brand_for(tenant, str(params["title"])),
                item_catalogs.today(),
                progress,
            )
            key = result_key(job.tenant_id, job.catalog_id, job.id)
            storage.put(key, pdf, "application/pdf")
            job.result_key = key
            job.page_count = pages
            job.progress = job.total
            job.status = "done"
            s.commit()
        except Exception as exc:  # noqa: BLE001 - report on the row, never crash the worker
            log.exception("price list pdf job %s failed", job_id)
            s.rollback()
            failed = s.get(CatalogOutputJob, job_id)
            if failed is not None:
                failed.status = "error"
                failed.error = f"{type(exc).__name__}: {exc}"[:500]
                s.commit()


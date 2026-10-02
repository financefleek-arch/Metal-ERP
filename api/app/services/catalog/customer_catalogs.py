"""Customer catalogs: build entries, store the PDF, keep versions, track staleness.

A customer catalog is a snapshot. Anything that changes the catalog's prices or items
afterwards calls `mark_stale`, and the user regenerates to get the next version.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    CatalogOutputJob,
    CustomerCatalog,
    ItemCategory,
    SupplierCatalog,
    SupplierCatalogItem,
    Tenant,
)
from app.services.catalog.catalog_pdf import Brand, CatalogEntry, CatalogPdfOptions, render_pdf
from app.services.catalog.storage import CatalogStorage

log = logging.getLogger("catalog.customer")

# Up to this many items are built inside the request; more run as a background job.
SYNC_ITEM_LIMIT = 100
# One customer catalog never exceeds this many items.
MAX_ITEMS = 5000
_CHUNK = 500


def pdf_key(tenant_id: str, catalog_id: str, customer_catalog_id: str) -> str:
    return f"catalog/{tenant_id}/{catalog_id}/customer/{customer_catalog_id}.pdf"


def options_to_json(opts: CatalogPdfOptions) -> dict[str, object]:
    return {
        "columns": opts.columns,
        "group_by": opts.group_by,
        "show_code": opts.show_code,
        "price_basis": opts.price_basis,
        "contents": opts.contents,
    }


def options_from_json(raw: dict[str, object]) -> CatalogPdfOptions:
    return CatalogPdfOptions(
        columns=int(raw["columns"]),  # type: ignore[call-overload]
        group_by=str(raw["group_by"]),
        show_code=bool(raw["show_code"]),
        price_basis=str(raw["price_basis"]),
        contents=bool(raw["contents"]),
    )


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


def load_entries(
    session: Session,
    storage: CatalogStorage,
    tenant_id: str,
    catalog_id: str,
    item_ids: list[str],
) -> list[CatalogEntry]:
    """Printable entries for `item_ids`, in that order. Only our price and our code leave
    this function: the cost price and the supplier's code are never read."""
    C = SupplierCatalogItem
    rows: dict[str, tuple[str, str, str | None, Decimal, int, str | None]] = {}
    for i in range(0, len(item_ids), _CHUNK):
        chunk = item_ids[i : i + _CHUNK]
        for item_id, code, name, price, pack, cat_name, key in session.execute(
            select(
                C.id,
                C.code,
                C.display_name,
                C.sell_price,
                C.pack_qty,
                ItemCategory.name,
                C.image_key,
            )
            .outerjoin(ItemCategory, ItemCategory.id == C.category_id)
            .where(C.tenant_id == tenant_id, C.catalog_id == catalog_id, C.id.in_(chunk))
        ):
            rows[item_id] = (code, name, cat_name, Decimal(price), int(pack), key)

    ordered = [rows[i] for i in item_ids if i in rows]
    images = storage.get_many([r[5] for r in ordered if r[5]])
    return [
        CatalogEntry(
            code=code,
            name=name,
            group=group,
            price=price,
            pack_qty=pack,
            image=images.get(key) if key else None,
        )
        for code, name, group, price, pack, key in ordered
    ]


def next_version(session: Session, catalog_id: str) -> int:
    top = session.scalar(
        select(func.max(CustomerCatalog.version)).where(CustomerCatalog.catalog_id == catalog_id)
    )
    return (top or 0) + 1


def store(
    session: Session,
    storage: CatalogStorage,
    *,
    catalog: SupplierCatalog,
    user_id: str | None,
    title: str,
    opts: CatalogPdfOptions,
    pdf: bytes,
    item_count: int,
    page_count: int,
) -> CustomerCatalog:
    """Save the PDF and its row as the next version. Retries once if two requests race for
    the same version number."""
    new_id = str(uuid.uuid4())
    key = pdf_key(catalog.tenant_id, catalog.id, new_id)
    storage.put(key, pdf, "application/pdf")
    for attempt in (1, 2):
        row = CustomerCatalog(
            id=new_id,
            tenant_id=catalog.tenant_id,
            catalog_id=catalog.id,
            created_by=user_id,
            version=next_version(session, catalog.id),
            title=title,
            options_json=options_to_json(opts),
            item_count=item_count,
            page_count=page_count,
            byte_size=len(pdf),
            pdf_key=key,
            stale=False,
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


def mark_stale(session: Session, catalog_id: str) -> None:
    """Flag every customer catalog of this supplier catalog as out of date."""
    session.execute(
        update(CustomerCatalog)
        .where(CustomerCatalog.catalog_id == catalog_id, CustomerCatalog.stale.is_(False))
        .values(stale=True)
    )


def run_catalog_pdf_job(job_id: str, storage: CatalogStorage) -> None:
    """Build the customer catalog for `job_id` and store it as a new version. Runs as a
    background task; reports on the job row and never raises."""
    with SessionLocal() as s:
        job = s.get(CatalogOutputJob, job_id)
        if job is None or job.params_json is None:
            return
        job.status = "running"
        s.commit()
        try:
            params = job.params_json
            catalog = s.get(SupplierCatalog, job.catalog_id)
            tenant = s.get(Tenant, job.tenant_id)
            if catalog is None or tenant is None:
                raise RuntimeError("catalog no longer exists")
            opts = options_from_json(params["options"])
            title = str(params["title"])
            entries = load_entries(
                s, storage, job.tenant_id, job.catalog_id, list(params["item_ids"])
            )

            def progress(done: int, total: int) -> None:
                job.progress = done
                s.commit()

            pdf, pages = render_pdf(
                entries, opts, brand_for(tenant, title), datetime.now(UTC).date(), progress
            )
            row = store(
                s,
                storage,
                catalog=catalog,
                user_id=job.created_by,
                title=title,
                opts=opts,
                pdf=pdf,
                item_count=len(entries),
                page_count=pages,
            )
            job.params_json = {**params, "customer_catalog_id": row.id}
            job.page_count = pages
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


def today() -> date:
    return datetime.now(UTC).date()


def create_job(
    session: Session,
    *,
    catalog: SupplierCatalog,
    user_id: str | None,
    item_ids: list[str],
    title: str,
    opts: CatalogPdfOptions,
) -> CatalogOutputJob:
    job = CatalogOutputJob(
        tenant_id=catalog.tenant_id,
        catalog_id=catalog.id,
        created_by=user_id,
        kind="catalog_pdf",
        params_json={"item_ids": item_ids, "title": title, "options": options_to_json(opts)},
        status="queued",
        progress=0,
        total=len(item_ids),
    )
    session.add(job)
    session.flush()
    return job

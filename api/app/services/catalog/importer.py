"""Import a supplier catalog PDF: extract -> clean -> group -> code -> price -> store.

One call, one transaction (the caller commits). Photos and the source PDF go to
object storage; if anything fails the request rolls back and any already-written
objects are orphans (harmless: content-addressed keys under the catalog's id).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog_groups import classify
from app.models import ItemCategory, SupplierCatalog, SupplierCatalogItem, Tenant
from app.services.catalog import names
from app.services.catalog.codes import allocate_codes, resolve_prefix
from app.services.catalog.extract_grid import extract
from app.services.catalog.groups import find_category, get_or_create_category
from app.services.catalog.pricing import sell_price
from app.services.catalog.storage import CatalogStorage, image_key, sha256_hex, source_key

_CONTENT_TYPES = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png"}


@dataclass
class ImportResult:
    catalog: SupplierCatalog
    created: bool
    skipped_cells: int = 0
    warnings: list[str] = field(default_factory=list)


def default_title(filename: str) -> str:
    stem = PurePath(filename).stem
    title = re.sub(r"[_\-]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip()
    return (title or "Supplier catalog")[:200]


def import_catalog(
    session: Session,
    storage: CatalogStorage,
    *,
    tenant: Tenant,
    user_id: str | None,
    filename: str,
    data: bytes,
    title: str | None = None,
    code_prefix: str | None = None,
) -> ImportResult:
    """Create the catalog from `data`, or return the existing one for the same file.

    Raises `NotACatalog` when no products can be read from the PDF.
    """
    sha = sha256_hex(data)
    existing = session.scalar(
        select(SupplierCatalog).where(
            SupplierCatalog.tenant_id == tenant.id, SupplierCatalog.source_sha256 == sha
        )
    )
    if existing is not None:
        return ImportResult(catalog=existing, created=False)

    result = extract(data)  # may raise NotACatalog

    catalog_id = str(uuid.uuid4())
    clean_title = (title or "").strip() or default_title(filename)
    prefix = resolve_prefix(tenant, clean_title, code_prefix)

    raws = [c.name_raw for c in result.cells]
    sup_codes = [c.supplier_code for c in result.cells]
    brands = names.infer_brands(raws, sup_codes)
    codes = allocate_codes(session, tenant.id, prefix, len(result.cells))

    auto_create = tenant.catalog_group_create_policy == "auto"
    cat_cache: dict[str, ItemCategory] = {}
    one = Decimal("1.000")

    catalog = SupplierCatalog(
        id=catalog_id,
        tenant_id=tenant.id,
        created_by=user_id,
        title=clean_title,
        source_filename=filename[:255],
        source_sha256=sha,
        source_key=source_key(tenant.id, catalog_id),
        page_count=result.page_count,
        item_count=len(result.cells),
        code_prefix=prefix,
        multiplier=one,
        rounding_step=1,
        status="extracting",
    )
    session.add(catalog)

    uploads: dict[str, tuple[bytes, str]] = {}
    for cell, code in zip(result.cells, codes, strict=True):
        display, carton = names.display_name(cell.name_raw, cell.supplier_code, brands)

        group = classify(cell.name_raw)
        category_id: str | None = None
        suggested: str | None = None
        if group is not None:
            if auto_create:
                category_id = get_or_create_category(session, tenant.id, group, cat_cache).id
            else:
                existing_cat = cat_cache.get(group.lower()) or find_category(
                    session, tenant.id, group
                )
                if existing_cat is not None:
                    cat_cache[group.lower()] = existing_cat
                    category_id = existing_cat.id
                else:
                    suggested = group

        sha_img = sha256_hex(cell.image)
        key = image_key(tenant.id, catalog_id, sha_img, cell.image_ext)
        uploads[key] = (cell.image, _CONTENT_TYPES.get(cell.image_ext, "application/octet-stream"))

        session.add(
            SupplierCatalogItem(
                tenant_id=tenant.id,
                catalog_id=catalog_id,
                page_no=cell.page_no,
                position=cell.position,
                supplier_code=cell.supplier_code,
                code=code,
                name_raw=cell.name_raw,
                display_name=display[:300],
                brand=names.brand_of(cell.name_raw, cell.supplier_code, brands),
                size_text=names.size_of(display),
                pack_qty=cell.pack_qty,
                carton_qty=carton,
                cost_price=cell.cost_price,
                sell_price=sell_price(cell.cost_price, one, 1),
                category_id=category_id,
                suggested_group=suggested,
                image_key=key,
                image_w=cell.image_w,
                image_h=cell.image_h,
                image_sha256=sha_img,
            )
        )
    session.flush()  # surfaces constraint problems before any object is written

    storage.put_many(
        [(k, b, ct) for k, (b, ct) in uploads.items()]
        + [(catalog.source_key or "", data, "application/pdf")]
    )
    catalog.status = "ready"
    session.flush()
    return ImportResult(
        catalog=catalog,
        created=True,
        skipped_cells=result.skipped_no_price,
        warnings=result.warnings,
    )

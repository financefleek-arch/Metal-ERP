"""Import a supplier catalog PDF: extract, clean, match products, group, code, price, store.

One call, one transaction (the caller commits). Photos and the source PDF go to object storage.

Memory does not grow with the file: the PDF is read from disk a page at a time, each page's photos
are stored as soon as that page is read and then dropped, and only small per-product records are
kept for the matching and coding passes. The source PDF is sent from disk in parts. If anything
fails, the photos already stored are deleted (and if even that fails they are harmless orphans:
content-addressed keys under the catalog's id).

Each row is matched to a *product* before it is given a code:
  1. same supplier + same supplier code  -> reuse that product (its code, group, name);
  2. otherwise a new product with the next firm-wide code and, if another product has the same
     normalised name, a suggestion the user can accept or dismiss.
Without a supplier there is no key to match on: every row becomes a new product (still with
name suggestions).
"""

from __future__ import annotations

import contextlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog_groups import classify
from app.models import (
    CatalogProduct,
    ItemCategory,
    SupplierCatalog,
    SupplierCatalogItem,
    SupplierPricePoint,
    Tenant,
)
from app.services.catalog import image_check, names, price_points, products, progress
from app.services.catalog.extract_grid import Cell, ExtractResult, extract_pages
from app.services.catalog.groups import find_category, get_or_create_category
from app.services.catalog.pricing import sell_price
from app.services.catalog.storage import CatalogStorage, image_key, sha256_hex, source_key

# products and rows are saved this many at a time, then dropped from the session
_BATCH = 250
_CONTENT_TYPES = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png"}


@dataclass
class ImportResult:
    catalog: SupplierCatalog
    created: bool
    skipped_cells: int = 0
    warnings: list[str] = field(default_factory=list)
    matched_items: int = 0  # rows that reused a product from an earlier catalog
    new_products: int = 0
    suggestions: int = 0  # rows with a same-name product to confirm
    price_changes: int = 0  # rows whose supplier price moved since the last list
    unread_price_lines: int = 0  # price lines with no picture: those products were not read


def default_title(filename: str) -> str:
    stem = PurePath(filename).stem
    title = re.sub(r"[_\-]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip()
    return (title or "Supplier catalog")[:200]


@dataclass
class _Plan:
    """What one cell becomes."""

    display: str
    carton: int | None
    category_id: str | None
    suggested_group: str | None
    product: CatalogProduct | None  # an existing product to reuse, else None (a new one)


def _store_page(
    storage: CatalogStorage, tenant_id: str, catalog_id: str, cells: list[Cell], uploaded: set[str]
) -> None:
    """Store one page's photos, note each one's key/hash/quality flag on its cell, drop the bytes."""
    batch: dict[str, tuple[bytes, str]] = {}
    for cell in cells:
        data = cell.image or b""
        sha_img = sha256_hex(data)
        key = image_key(tenant_id, catalog_id, sha_img, cell.image_ext)
        cell.image_sha256, cell.image_key = sha_img, key
        cell.image_flag = image_check.check(data)
        if key not in uploaded:
            batch[key] = (data, _CONTENT_TYPES.get(cell.image_ext, "application/octet-stream"))
        cell.image = None
    uploaded.update(batch)  # before the upload, so a failure part-way is still cleaned up
    storage.put_many([(k, b, ct) for k, (b, ct) in batch.items()])


def import_catalog(
    session: Session,
    storage: CatalogStorage,
    *,
    tenant: Tenant,
    user_id: str | None,
    filename: str,
    pdf_path: str,
    sha256: str,
    title: str | None = None,
    supplier_party_id: str | None = None,
    group_map: dict[str, str] | None = None,
    progress_id: str | None = None,
) -> ImportResult:
    """Create the catalog from the PDF at `pdf_path` (`sha256` is its hash), or return the
    existing one for the same file.

    `supplier_party_id` is the supplier's party (already checked by the caller); it is what
    lets a later PDF find the same products again.

    Raises `NotACatalog` when no products can be read from the PDF.
    """
    existing = session.scalar(
        select(SupplierCatalog).where(
            SupplierCatalog.tenant_id == tenant.id, SupplierCatalog.source_sha256 == sha256
        )
    )
    if existing is not None:
        return ImportResult(catalog=existing, created=False)

    catalog_id = str(uuid.uuid4())
    uploaded: set[str] = set()
    try:
        return _build_catalog(
            session,
            storage,
            tenant=tenant,
            user_id=user_id,
            filename=filename,
            pdf_path=pdf_path,
            sha=sha256,
            title=title,
            supplier_party_id=supplier_party_id,
            group_map=group_map,
            catalog_id=catalog_id,
            uploaded=uploaded,
            progress_id=progress_id,
        )
    except BaseException:
        # best effort: the leftovers are harmless orphans
        with contextlib.suppress(Exception):
            storage.delete_many(list(uploaded) + [source_key(tenant.id, catalog_id)])
        raise


@dataclass
class _Run:
    """What every batch of one import shares."""

    tenant: Tenant
    catalog_id: str
    supplier_party_id: str | None
    group_map: dict[str, str] | None
    brands: set[str]
    auto_create: bool
    cat_cache: dict[str, ItemCategory] = field(default_factory=dict)
    fresh_ids: set[str] = field(default_factory=set)  # products this file created so far
    matched: int = 0
    new_products: int = 0
    suggested: int = 0
    price_changes: int = 0


def _forget_batch(session: Session) -> None:
    """Drop the batch's rows from the session. They are saved; keeping thousands of loaded objects
    for the length of the import is what made memory grow with the number of products."""
    for obj in list(session.identity_map.values()):
        if isinstance(obj, CatalogProduct | SupplierCatalogItem | SupplierPricePoint):
            session.expunge(obj)


def _build_catalog(
    session: Session,
    storage: CatalogStorage,
    *,
    tenant: Tenant,
    user_id: str | None,
    filename: str,
    pdf_path: str,
    sha: str,
    title: str | None,
    supplier_party_id: str | None,
    group_map: dict[str, str] | None,
    catalog_id: str,
    uploaded: set[str],
    progress_id: str | None,
) -> ImportResult:
    # --- the photos: stored a page at a time; only small records stay in memory --------------
    result = ExtractResult()
    cells: list[Cell] = []
    for page_cells in extract_pages(pdf_path, result):  # may raise NotACatalog
        _store_page(storage, tenant.id, catalog_id, page_cells, uploaded)
        cells.extend(page_cells)
        progress.report(
            tenant.id, progress_id, "reading", page_cells[-1].page_no, result.page_count
        )

    clean_title = (title or "").strip() or default_title(filename)
    brands = names.infer_brands([c.name_raw for c in cells], [c.supplier_code for c in cells])

    catalog = SupplierCatalog(
        id=catalog_id,
        tenant_id=tenant.id,
        supplier_party_id=supplier_party_id,
        created_by=user_id,
        title=clean_title,
        source_filename=filename[:255],
        source_sha256=sha,
        source_key=source_key(tenant.id, catalog_id),
        page_count=result.page_count,
        item_count=len(cells),
        bulk_margin_pct=Decimal("0.00"),
        rounding_step=1,
        status="extracting",
    )
    session.add(catalog)
    session.flush()

    # --- the products and rows: a batch at a time, in reading order ---------------------------
    run = _Run(
        tenant=tenant,
        catalog_id=catalog_id,
        supplier_party_id=supplier_party_id,
        group_map=group_map,
        brands=brands,
        auto_create=tenant.catalog_group_create_policy == "auto",
    )
    for start in range(0, len(cells), _BATCH):
        _import_batch(session, run, cells[start : start + _BATCH])
        _forget_batch(session)
        progress.report(
            tenant.id, progress_id, "saving", min(start + _BATCH, len(cells)), len(cells)
        )

    storage.put_file(catalog.source_key or "", pdf_path, "application/pdf")
    catalog.status = "ready"
    session.flush()
    return ImportResult(
        catalog=catalog,
        created=True,
        skipped_cells=result.skipped_no_price,
        warnings=result.warnings,
        matched_items=run.matched,
        new_products=run.new_products,
        suggestions=run.suggested,
        price_changes=run.price_changes,
        unread_price_lines=result.unread_price_lines,
    )


def _import_batch(session: Session, run: _Run, batch: list[Cell]) -> None:
    tenant = run.tenant
    known = (
        products.products_by_supplier_codes(
            session, tenant.id, run.supplier_party_id, [c.supplier_code for c in batch]
        )
        if run.supplier_party_id
        else {}
    )

    # --- pass 1: decide what every cell becomes -------------------------------------
    plans: list[_Plan] = []
    for cell in batch:
        display, carton = names.display_name(cell.name_raw, cell.supplier_code, run.brands)
        hit = known.get(cell.supplier_code)
        if hit is not None:
            # a product we already know: its code, group and name win over the rules
            plans.append(_Plan(hit.display_name, carton, hit.category_id, None, hit))
            continue
        group = classify(cell.name_raw)
        if group is not None and run.group_map:
            group = run.group_map.get(" ".join(group.lower().split()), group)
        category_id: str | None = None
        suggested: str | None = None
        if group is not None:
            if run.auto_create:
                cat = get_or_create_category(session, tenant.id, group, run.cat_cache)
                category_id = cat.id
            else:
                found = run.cat_cache.get(group.lower()) or find_category(session, tenant.id, group)
                if found is not None:
                    run.cat_cache[group.lower()] = found
                    category_id = found.id
                else:
                    suggested = group
        plans.append(_Plan(display, carton, category_id, suggested, None))

    # --- pass 2: issue codes for the new products, in reading order -------------------
    new_idx = [i for i, plan in enumerate(plans) if plan.product is None]
    new_codes: dict[int, str] = dict(
        zip(new_idx, products.issue_codes(session, tenant, len(new_idx)), strict=True)
    )

    # --- one product per new row ------------------------------------------------------
    created: list[CatalogProduct | None] = []
    for i, (cell, plan) in enumerate(zip(batch, plans, strict=True)):
        if plan.product is not None:
            created.append(None)
            continue
        prod = products.new_product(
            tenant_id=tenant.id,
            supplier_party_id=run.supplier_party_id,
            supplier_code=cell.supplier_code,
            code=new_codes[i],
            category_id=plan.category_id,
            display_name=plan.display[:300],
        )
        session.add(prod)
        created.append(prod)
    session.flush()  # product ids, and constraint problems before the rows

    # same-name products from earlier files or other suppliers -> suggestions to confirm
    fresh = [p for p in created if p is not None]
    suggestions = products.suggest_by_name(
        session,
        tenant.id,
        {p.id: p.name_normalized for p in fresh},
        exclude_ids=run.fresh_ids | {p.id for p in fresh},
    )
    run.fresh_ids.update(p.id for p in fresh)
    run.new_products += len(fresh)

    # --- the catalog rows -------------------------------------------------------------
    made: list[SupplierCatalogItem] = []
    for cell, plan, new in zip(batch, plans, created, strict=True):
        prod = plan.product or new
        assert prod is not None
        if plan.product is not None:
            run.matched += 1
        sug = suggestions.get(new.id) if new is not None else None
        if sug is not None:
            run.suggested += 1

        row = SupplierCatalogItem(
            tenant_id=tenant.id,
            catalog_id=run.catalog_id,
            product_id=prod.id,
            suggested_product_id=sug.id if sug is not None else None,
            page_no=cell.page_no,
            position=cell.position,
            supplier_code=cell.supplier_code,
            code=prod.code,
            name_raw=cell.name_raw,
            display_name=prod.display_name[:300],
            brand=names.brand_of(cell.name_raw, cell.supplier_code, run.brands),
            size_text=names.size_of(prod.display_name),
            pack_qty=cell.pack_qty,
            carton_qty=plan.carton,
            cost_price=cell.cost_price,
            sell_price=sell_price(cell.cost_price, Decimal("0.00"), 1),
            category_id=prod.category_id,
            suggested_group=plan.suggested_group,
            image_key=cell.image_key,
            image_w=cell.image_w,
            image_h=cell.image_h,
            image_sha256=cell.image_sha256,
            image_flag=cell.image_flag,
        )
        session.add(row)
        made.append(row)
    session.flush()

    # what changed since the last price list for these products, and remember this one's prices
    prev = price_points.latest_quotes(
        session,
        tenant.id,
        [r.product_id for r in made if r.product_id],
        exclude_source_id=run.catalog_id,
    )
    for r in made:
        r.price_change = price_points.change_of(prev.get(r.product_id or ""), r.cost_price)
        run.price_changes += r.price_change in ("up", "down")
    price_points.record_quotes(
        session,
        tenant_id=tenant.id,
        supplier_party_id=run.supplier_party_id,
        catalog_id=run.catalog_id,
        rows=[(r.product_id, r.cost_price, r.pack_qty) for r in made if r.product_id],
        on=date.today(),
    )
    session.flush()

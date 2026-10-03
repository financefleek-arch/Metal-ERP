"""Import a supplier catalog PDF: extract, clean, match products, group, code, price, store.

One call, one transaction (the caller commits). Photos and the source PDF go to object storage;
if anything fails the request rolls back and any already-written objects are orphans (harmless:
content-addressed keys under the catalog's id).

Each row is matched to a *product* before it is given a code:
  1. same supplier + same supplier code  -> reuse that product (its code, group, name);
  2. otherwise a new product, with a code in its group's series, and, if another product has the
     same normalised name, a suggestion the user can accept or dismiss.
Without a supplier there is no key to match on: every row becomes a new product (still with
name suggestions).
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
from app.models import (
    CatalogProduct,
    ItemCategory,
    SupplierCatalog,
    SupplierCatalogItem,
    Tenant,
)
from app.services.catalog import names, products
from app.services.catalog.codes import normalize_prefix
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
    matched_items: int = 0  # rows that reused a product from an earlier catalog
    new_products: int = 0
    suggestions: int = 0  # rows with a same-name product to confirm


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
    supplier_party_id: str | None = None,
) -> ImportResult:
    """Create the catalog from `data`, or return the existing one for the same file.

    `supplier_party_id` is the supplier's party (already checked by the caller); it is what
    lets a later PDF find the same products again. `code_prefix`, if given, is this catalog's
    prefix for products that have no group; otherwise the firm's fallback prefix is used.

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
    prefix = normalize_prefix(code_prefix) or products.fallback_prefix(tenant)

    raws = [c.name_raw for c in result.cells]
    sup_codes = [c.supplier_code for c in result.cells]
    brands = names.infer_brands(raws, sup_codes)

    known = (
        products.products_by_supplier_code(session, tenant.id, supplier_party_id)
        if supplier_party_id
        else {}
    )
    auto_create = tenant.catalog_group_create_policy == "auto"
    cat_cache: dict[str, ItemCategory] = {}
    cat_by_id: dict[str, ItemCategory] = {}

    # --- pass 1: decide what every cell becomes -------------------------------------
    plans: list[_Plan] = []
    for cell in result.cells:
        display, carton = names.display_name(cell.name_raw, cell.supplier_code, brands)
        hit = known.get(cell.supplier_code)
        if hit is not None:
            # a product we already know: its code, group and name win over the rules
            plans.append(_Plan(hit.display_name, carton, hit.category_id, None, hit))
            continue
        group = classify(cell.name_raw)
        category_id: str | None = None
        suggested: str | None = None
        if group is not None:
            if auto_create:
                cat = get_or_create_category(session, tenant.id, group, cat_cache)
                category_id = cat.id
                cat_by_id[cat.id] = cat
            else:
                found = cat_cache.get(group.lower()) or find_category(session, tenant.id, group)
                if found is not None:
                    cat_cache[group.lower()] = found
                    cat_by_id[found.id] = found
                    category_id = found.id
                else:
                    suggested = group
        plans.append(_Plan(display, carton, category_id, suggested, None))

    # --- pass 2: issue codes for the new products, one block per group ---------------
    by_group: dict[str | None, list[int]] = {}
    for i, plan in enumerate(plans):
        if plan.product is None:
            by_group.setdefault(plan.category_id, []).append(i)
    new_codes: dict[int, str] = {}
    for category_id, idxs in by_group.items():
        category = cat_by_id.get(category_id) if category_id else None
        if category_id and category is None:  # defensive: a category we did not just look up
            category = session.get(ItemCategory, category_id)
        codes = products.issue_codes(session, tenant, category, len(idxs))
        new_codes.update(zip(idxs, codes, strict=True))

    # --- the catalog + one product per new row + the rows ----------------------------
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
        item_count=len(result.cells),
        code_prefix=prefix,
        bulk_margin_pct=Decimal("0.00"),
        rounding_step=1,
        status="extracting",
    )
    session.add(catalog)

    created: list[CatalogProduct | None] = []
    for i, (cell, plan) in enumerate(zip(result.cells, plans, strict=True)):
        if plan.product is not None:
            created.append(None)
            continue
        prod = products.new_product(
            tenant_id=tenant.id,
            supplier_party_id=supplier_party_id,
            supplier_code=cell.supplier_code,
            code=new_codes[i],
            category_id=plan.category_id,
            display_name=plan.display[:300],
        )
        session.add(prod)
        created.append(prod)
    session.flush()  # product ids, and constraint problems before any object is written

    # same-name products from earlier files or other suppliers -> suggestions to confirm
    fresh = [p for p in created if p is not None]
    suggestions = products.suggest_by_name(
        session,
        tenant.id,
        {p.id: p.name_normalized for p in fresh},
        exclude_ids={p.id for p in fresh},
    )

    uploads: dict[str, tuple[bytes, str]] = {}
    matched = 0
    suggested_n = 0
    for cell, plan, new in zip(result.cells, plans, created, strict=True):
        prod = plan.product or new
        assert prod is not None
        if plan.product is not None:
            matched += 1
        sug = suggestions.get(new.id) if new is not None else None
        if sug is not None:
            suggested_n += 1

        sha_img = sha256_hex(cell.image)
        key = image_key(tenant.id, catalog_id, sha_img, cell.image_ext)
        uploads[key] = (cell.image, _CONTENT_TYPES.get(cell.image_ext, "application/octet-stream"))

        session.add(
            SupplierCatalogItem(
                tenant_id=tenant.id,
                catalog_id=catalog_id,
                product_id=prod.id,
                suggested_product_id=sug.id if sug is not None else None,
                page_no=cell.page_no,
                position=cell.position,
                supplier_code=cell.supplier_code,
                code=prod.code,
                name_raw=cell.name_raw,
                display_name=prod.display_name[:300],
                brand=names.brand_of(cell.name_raw, cell.supplier_code, brands),
                size_text=names.size_of(prod.display_name),
                pack_qty=cell.pack_qty,
                carton_qty=plan.carton,
                cost_price=cell.cost_price,
                sell_price=sell_price(cell.cost_price, Decimal("0.00"), 1),
                category_id=prod.category_id,
                suggested_group=plan.suggested_group,
                image_key=key,
                image_w=cell.image_w,
                image_h=cell.image_h,
                image_sha256=sha_img,
            )
        )
    session.flush()

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
        matched_items=matched,
        new_products=len(fresh),
        suggestions=suggested_n,
    )

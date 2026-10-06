"""Supplier Catalog API: /api/supplier-catalogs.

The whole router is gated by `ext_supplier_catalog`: a tenant without the flag
gets a 404 on every route (the module is invisible, not just hidden), same as
the inward module. Write actions need owner/accountant; viewers can read.

S1: upload + extract, review (list, edit, bulk edit), groups, photos, delete.
Pricing, labels, customer catalog and Tally land in later slices.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from app.deps import SessionDep, get_current_user, require_write
from app.models import (
    CatalogOutputJob,
    CatalogProduct,
    ItemCategory,
    Party,
    SupplierCatalog,
    SupplierCatalogItem,
    SupplierPricePoint,
    Tenant,
    User,
)
from app.schemas_catalog import (
    BulkPatch,
    BulkResult,
    CatalogDetail,
    CatalogItemOut,
    CatalogItemPatch,
    CatalogListItem,
    CatalogPatch,
    CatalogUploadOut,
    GroupSummary,
    ItemFilter,
    LabelRequest,
    LinkProductIn,
    OutputJobOut,
    ProductRef,
    SupplierCatalogsOut,
)
from app.services import media as media_svc
from app.services.catalog import image_url, output_jobs, price_points
from app.services.catalog import labels as labels_svc
from app.services.catalog import products as products_svc
from app.services.catalog import supplier_defaults as defaults_svc
from app.services.catalog import suppliers as suppliers_svc
from app.services.catalog.barcode import modules as barcode_modules
from app.services.catalog.extract_grid import NotACatalog
from app.services.catalog.groups import clean_group_name, get_or_create_category
from app.services.catalog.importer import import_catalog
from app.services.catalog.pricing import effective_margin, sell_price
from app.services.catalog.reprice import reprice
from app.services.catalog.storage import CatalogStorage, get_storage
from app.services.pagination import finish_page, paginate

router = APIRouter(prefix="/api/supplier-catalogs", tags=["supplier-catalog"])

MAX_UPLOAD_BYTES = 60 * 1024 * 1024
_PDF_MAGIC = b"%PDF-"


def _gate(session: SessionDep, user: User) -> User:
    tenant = session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.ext_supplier_catalog:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return user


def require_catalog(
    session: SessionDep, user: Annotated[User, Depends(get_current_user)]
) -> User:
    return _gate(session, user)


def require_catalog_write(
    session: SessionDep, user: Annotated[User, Depends(require_write)]
) -> User:
    return _gate(session, user)


CatalogUser = Annotated[User, Depends(require_catalog)]
CatalogWriteUser = Annotated[User, Depends(require_catalog_write)]
StorageDep = Annotated[CatalogStorage, Depends(get_storage)]


def _get_catalog(session: SessionDep, tenant_id: str, catalog_id: str) -> SupplierCatalog:
    cat = session.scalar(
        select(SupplierCatalog).where(
            SupplierCatalog.id == catalog_id, SupplierCatalog.tenant_id == tenant_id
        )
    )
    if cat is None:
        raise HTTPException(status_code=404, detail="Catalog not found")
    return cat


def _get_item(
    session: SessionDep, tenant_id: str, catalog_id: str, item_id: str
) -> SupplierCatalogItem:
    item = session.scalar(
        select(SupplierCatalogItem).where(
            SupplierCatalogItem.id == item_id,
            SupplierCatalogItem.catalog_id == catalog_id,
            SupplierCatalogItem.tenant_id == tenant_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


# --------------------------------------------------------------------------
# catalogs
# --------------------------------------------------------------------------


@router.get("", response_model=list[CatalogListItem])
def list_catalogs(session: SessionDep, user: CatalogUser) -> list[CatalogListItem]:
    rows = list(
        session.scalars(
            select(SupplierCatalog)
            .where(SupplierCatalog.tenant_id == user.tenant_id)
            .order_by(SupplierCatalog.created_at.desc())
        )
    )
    names = _supplier_names(session, {r.supplier_party_id for r in rows if r.supplier_party_id})
    out: list[CatalogListItem] = []
    for r in rows:
        item = CatalogListItem.model_validate(r, from_attributes=True)
        item.supplier_name = names.get(r.supplier_party_id) if r.supplier_party_id else None
        out.append(item)
    return out


def _supplier_names(session: SessionDep, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {
        pid: name
        for pid, name in session.execute(
            select(Party.id, Party.legal_name).where(Party.id.in_(ids))
        )
    }


def _check_supplier(session: SessionDep, tenant_id: str, party_id: str) -> Party:
    party = session.scalar(select(Party).where(Party.id == party_id, Party.tenant_id == tenant_id))
    if party is None:
        raise HTTPException(status_code=422, detail="That supplier was not found.")
    if party.status != "active":
        raise HTTPException(
            status_code=422, detail=f"{party.legal_name} is archived. Restore it first."
        )
    if party.role not in ("supplier", "both"):
        raise HTTPException(
            status_code=422, detail=f"{party.legal_name} is not marked as a supplier."
        )
    return party


@router.get("/by-supplier/{party_id}", response_model=SupplierCatalogsOut)
def catalogs_of_supplier(
    party_id: str, session: SessionDep, user: CatalogUser
) -> SupplierCatalogsOut:
    """What we hold from one supplier: their catalogs and how many products."""
    party = session.scalar(
        select(Party).where(Party.id == party_id, Party.tenant_id == user.tenant_id)
    )
    if party is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    summary = suppliers_svc.supplier_summary(session, user.tenant_id, party_id)
    items = []
    for c in summary.catalogs:
        row = CatalogListItem.model_validate(c, from_attributes=True)
        row.supplier_name = party.legal_name
        items.append(row)
    return SupplierCatalogsOut(
        catalogs=items,
        product_count=summary.product_count,
        promoted_count=summary.promoted_count,
    )


@router.post("", response_model=CatalogUploadOut, status_code=status.HTTP_201_CREATED)
def upload_catalog(
    response: Response,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    supplier_party_id: Annotated[str | None, Form()] = None,
) -> CatalogUploadOut:
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That file is over 60 MB. Split the catalog and upload the parts.",
        )
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a PDF file."
        )

    supplier_id = (supplier_party_id or "").strip() or None
    if supplier_id:
        _check_supplier(session, user.tenant_id, supplier_id)

    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    defaults = defaults_svc.get(session, user.tenant_id, supplier_id) if supplier_id else None
    try:
        result = import_catalog(
            session,
            storage,
            tenant=tenant,
            user_id=user.id,
            filename=file.filename or "catalog.pdf",
            data=data,
            title=title,
            supplier_party_id=supplier_id,
            group_map=defaults_svc.group_lookup(defaults),
        )
    except NotACatalog as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result.created and defaults is not None:
        defaults_svc.apply_to_catalog(session, result.catalog, defaults)
        plan = defaults_svc.add_automatically(session, result.catalog, defaults)
        if plan is not None:
            rows = [r for r in result.catalog.items if r.included]
            pairs = media_svc.items_needing_photos(session, user.tenant_id, rows)
            if pairs:
                session.commit()  # the background task reads these rows from its own session
                background.add_task(media_svc.copy_catalog_photos, user.tenant_id, pairs, storage)

    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    out = CatalogUploadOut.model_validate(result.catalog, from_attributes=True)
    out.already_exists = not result.created
    out.skipped_cells = result.skipped_cells
    out.warnings = result.warnings[:50]
    out.matched_items = result.matched_items
    out.new_products = result.new_products
    out.suggestions = result.suggestions
    out.price_changes = result.price_changes
    out.unread_price_lines = result.unread_price_lines
    if result.catalog.supplier_party_id:
        out.supplier_name = _supplier_names(session, {result.catalog.supplier_party_id}).get(
            result.catalog.supplier_party_id
        )
    return out


def _detail(session: SessionDep, cat: SupplierCatalog) -> CatalogDetail:
    n = session.scalar(
        select(func.count())
        .select_from(SupplierCatalogItem)
        .where(
            SupplierCatalogItem.catalog_id == cat.id,
            SupplierCatalogItem.item_margin_pct.is_not(None),
        )
    )
    out = CatalogDetail.model_validate(cat, from_attributes=True)
    out.item_margin_count = int(n or 0)
    out.photo_check_count = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierCatalogItem)
            .where(
                SupplierCatalogItem.catalog_id == cat.id,
                SupplierCatalogItem.image_flag.is_not(None),
            )
        )
        or 0
    )
    out.price_change_count = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierCatalogItem)
            .where(
                SupplierCatalogItem.catalog_id == cat.id,
                SupplierCatalogItem.price_change.in_(("up", "down")),
            )
        )
        or 0
    )
    out.suggestion_count = int(
        session.scalar(
            select(func.count())
            .select_from(SupplierCatalogItem)
            .where(
                SupplierCatalogItem.catalog_id == cat.id,
                SupplierCatalogItem.suggested_product_id.is_not(None),
            )
        )
        or 0
    )
    if cat.supplier_party_id:
        out.supplier_name = _supplier_names(session, {cat.supplier_party_id}).get(
            cat.supplier_party_id
        )
    return out


@router.get("/{catalog_id}", response_model=CatalogDetail)
def get_catalog(catalog_id: str, session: SessionDep, user: CatalogUser) -> CatalogDetail:
    return _detail(session, _get_catalog(session, user.tenant_id, catalog_id))


@router.patch("/{catalog_id}", response_model=CatalogDetail)
def patch_catalog(
    catalog_id: str, body: CatalogPatch, session: SessionDep, user: CatalogWriteUser
) -> CatalogDetail:
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    if body.title is not None:
        cat.title = body.title.strip()
    if body.supplier_party_id is not None and body.supplier_party_id != cat.supplier_party_id:
        _set_supplier(session, user.tenant_id, cat, body.supplier_party_id)
    pricing_changed = False
    if body.bulk_margin_pct is not None and body.bulk_margin_pct != cat.bulk_margin_pct:
        cat.bulk_margin_pct = body.bulk_margin_pct
        pricing_changed = True
    if body.rounding_step is not None and body.rounding_step != cat.rounding_step:
        cat.rounding_step = body.rounding_step
        pricing_changed = True
    session.flush()
    if pricing_changed:
        reprice(session, cat)
    return _detail(session, cat)


def _set_supplier(
    session: SessionDep, tenant_id: str, cat: SupplierCatalog, party_id: str
) -> None:
    """Attach the supplier to a catalog uploaded without one, so later PDFs can match it."""
    if cat.supplier_party_id is not None:
        raise HTTPException(status_code=409, detail="This catalog already has a supplier.")
    _check_supplier(session, tenant_id, party_id)
    mine = list(
        session.scalars(
            select(CatalogProduct)
            .join(SupplierCatalogItem, SupplierCatalogItem.product_id == CatalogProduct.id)
            .where(SupplierCatalogItem.catalog_id == cat.id)
        )
    )
    if any(p.supplier_party_id is not None for p in mine):
        raise HTTPException(status_code=409, detail="Some products already belong to a supplier.")
    known = products_svc.products_by_supplier_code(session, tenant_id, party_id)
    clash = [p.supplier_code for p in mine if p.supplier_code in known]
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"This supplier already has {len(clash)} of these product codes "
            f"(for example {clash[0]}) from another catalog.",
        )
    for p in mine:
        p.supplier_party_id = party_id
    cat.supplier_party_id = party_id
    session.flush()


@router.delete("/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_catalog(
    catalog_id: str, session: SessionDep, user: CatalogWriteUser, storage: StorageDep
) -> None:
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    promoted = session.scalar(
        select(func.count())
        .select_from(SupplierCatalogItem)
        .where(SupplierCatalogItem.catalog_id == cat.id, SupplierCatalogItem.item_id.is_not(None))
    )
    if promoted:
        raise HTTPException(
            status_code=409,
            detail=f"{promoted} items from this catalog are already in your item list. "
            "Remove them there first.",
        )
    keys = {
        k
        for (k,) in session.execute(
            select(SupplierCatalogItem.image_key)
            .where(
                SupplierCatalogItem.catalog_id == cat.id,
                SupplierCatalogItem.image_key.is_not(None),
            )
            .distinct()
        )
        if k
    }
    if cat.source_key:
        keys.add(cat.source_key)
    for job in session.scalars(
        select(CatalogOutputJob).where(CatalogOutputJob.catalog_id == cat.id)
    ):
        if job.result_key:
            keys.add(job.result_key)
        session.delete(job)
    session.delete(cat)
    session.flush()
    # leftover objects are harmless; the rows are already gone
    with contextlib.suppress(Exception):
        storage.delete_many(keys)


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------


def _like(value: str) -> str:
    esc = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _filter_clauses(f: ItemFilter) -> list[ColumnElement[bool]]:
    C = SupplierCatalogItem
    out: list[ColumnElement[bool]] = []
    if f.q:
        for word in f.q.split():
            pat = _like(word)
            out.append(
                or_(
                    C.display_name.ilike(pat, escape="\\"),
                    C.supplier_code.ilike(pat, escape="\\"),
                    C.code.ilike(pat, escape="\\"),
                    C.name_raw.ilike(pat, escape="\\"),
                    C.brand.ilike(pat, escape="\\"),
                )
            )
    if f.no_group:
        out.append(C.category_id.is_(None))
    elif f.category_id:
        out.append(C.category_id == f.category_id)
    if f.included is not None:
        out.append(C.included.is_(f.included))
    if f.brand:
        out.append(func.lower(C.brand) == f.brand.lower())
    if f.has_item_margin is True:
        out.append(C.item_margin_pct.is_not(None))
    elif f.has_item_margin is False:
        out.append(C.item_margin_pct.is_(None))
    if f.has_suggestion is True:
        out.append(C.suggested_product_id.is_not(None))
    if f.photo_check is True:
        out.append(C.image_flag.is_not(None))
    if f.price_changed is True:
        out.append(C.price_change.in_(("up", "down")))
    if f.not_added is True:
        out.append(C.item_id.is_(None))
    elif f.not_added is False:
        out.append(C.item_id.is_not(None))
    return out


def _category_names(session: SessionDep, tenant_id: str) -> dict[str, str]:
    return {
        cid: name
        for cid, name in session.execute(
            select(ItemCategory.id, ItemCategory.name).where(ItemCategory.tenant_id == tenant_id)
        )
    }


def _barcode(code: str) -> str | None:
    try:
        return barcode_modules(code)
    except ValueError:
        return None


def _product_maps(
    session: SessionDep, items: list[SupplierCatalogItem]
) -> tuple[dict[str, CatalogProduct], dict[str, CatalogProduct]]:
    """(own product by id, suggested product by id) for these rows."""
    own_ids = {i.product_id for i in items if i.product_id}
    sug_ids = {i.suggested_product_id for i in items if i.suggested_product_id}
    found: dict[str, CatalogProduct] = {}
    wanted = list(own_ids | sug_ids)
    for k in range(0, len(wanted), 500):
        for p in session.scalars(
            select(CatalogProduct).where(CatalogProduct.id.in_(wanted[k : k + 500]))
        ):
            found[p.id] = p
    return found, found


def _name_suggestion_suppliers(
    session: SessionDep, outs: list[CatalogItemOut], prods: dict[str, CatalogProduct]
) -> None:
    """Fill in who offers each suggested product."""
    ids = {
        prods[o.suggestion.product_id].supplier_party_id
        for o in outs
        if o.suggestion and o.suggestion.product_id in prods
    }
    names = _supplier_names(session, {i for i in ids if i})
    for o in outs:
        if o.suggestion and o.suggestion.product_id in prods:
            sid = prods[o.suggestion.product_id].supplier_party_id
            o.suggestion.supplier_name = names.get(sid) if sid else None


def _changed_since(current: datetime, expected: datetime) -> bool:
    """Has the row changed after the moment the editor saw it? (a second of slack covers
    databases that keep whole seconds)"""
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=UTC)
    return abs((current - expected).total_seconds()) >= 1


def _price_notes(
    session: SessionDep,
    tenant_id: str,
    catalog_id: str,
    pairs: list[tuple[CatalogItemOut, SupplierCatalogItem]],
) -> None:
    """Add the price history to rows already built: the price before, and what the last bill
    charged."""
    prev = price_points.latest_quotes(
        session,
        tenant_id,
        [r.product_id for _, r in pairs if r.product_id],
        exclude_source_id=catalog_id,
    )
    paid = price_points.last_paid(session, tenant_id, [r.item_id for _, r in pairs if r.item_id])
    for out, row in pairs:
        out.price_change = row.price_change
        if row.product_id in prev and row.price_change in ("up", "down"):
            out.previous_cost = prev[row.product_id]
        if row.item_id in paid:
            out.last_paid, out.last_paid_on = paid[row.item_id]


def _item_out(
    catalog_id: str,
    it: SupplierCatalogItem,
    cats: dict[str, str],
    prods: dict[str, CatalogProduct] | None = None,
) -> CatalogItemOut:
    prods = prods or {}
    sug = prods.get(it.suggested_product_id) if it.suggested_product_id else None
    return CatalogItemOut(
        id=it.id,
        page_no=it.page_no,
        position=it.position,
        code=it.code,
        supplier_code=it.supplier_code,
        display_name=it.display_name,
        name_raw=it.name_raw,
        updated_at=it.updated_at,
        image_flag=it.image_flag,
        brand=it.brand,
        size_text=it.size_text,
        pack_qty=it.pack_qty,
        carton_qty=it.carton_qty,
        cost_price=it.cost_price,
        item_margin_pct=it.item_margin_pct,
        sell_price=it.sell_price,
        category_id=it.category_id,
        category_name=cats.get(it.category_id) if it.category_id else None,
        suggested_group=it.suggested_group,
        included=it.included,
        image_url=(
            f"/api/supplier-catalogs/{catalog_id}/items/{it.id}/image"
            f"?{image_url.sign_query(it.id)}&v={(it.image_sha256 or '')[:8]}"  # v: a new photo
            if it.image_key
            else None
        ),
        barcode=_barcode(it.code),
        product_id=it.product_id,
        suggestion=(
            ProductRef(product_id=sug.id, code=sug.code, name=sug.display_name) if sug else None
        ),
        item_id=it.item_id,
        tally_status=it.tally_status,
    )


@router.get("/{catalog_id}/items", response_model=list[CatalogItemOut])
def list_items(
    catalog_id: str,
    response: Response,
    session: SessionDep,
    user: CatalogUser,
    q: str | None = Query(default=None, max_length=100),
    category_id: str | None = None,
    no_group: bool = False,
    included: bool | None = None,
    brand: str | None = None,
    has_item_margin: bool | None = None,
    has_suggestion: bool | None = None,
    not_added: bool | None = None,
    price_changed: bool | None = None,
    photo_check: bool | None = None,
    limit: int | None = Query(default=100, ge=1, le=200),
    cursor: str | None = None,
) -> list[CatalogItemOut]:
    _get_catalog(session, user.tenant_id, catalog_id)
    C = SupplierCatalogItem
    flt = ItemFilter(
        q=q,
        category_id=category_id,
        no_group=no_group,
        included=included,
        brand=brand,
        has_item_margin=has_item_margin,
        has_suggestion=has_suggestion,
        not_added=not_added,
        price_changed=price_changed,
        photo_check=photo_check,
    )
    base = select(C).where(
        C.tenant_id == user.tenant_id, C.catalog_id == catalog_id, *_filter_clauses(flt)
    )

    total = session.scalar(select(func.count()).select_from(base.subquery()))
    response.headers["X-Total-Count"] = str(total or 0)

    stmt, _ = paginate(
        base,
        order_cols=[C.page_no, C.position, C.id],
        directions=["asc", "asc", "asc"],
        limit=limit,
        cursor=cursor,
    )
    rows = list(session.scalars(stmt))
    page = finish_page(
        rows, limit=limit, key_of=lambda r: [r.page_no, r.position, r.id], response=response
    )
    cats = _category_names(session, user.tenant_id)
    prods, _ = _product_maps(session, page)
    outs = [_item_out(catalog_id, it, cats, prods) for it in page]
    _price_notes(session, user.tenant_id, catalog_id, list(zip(outs, page, strict=True)))
    _name_suggestion_suppliers(session, outs, prods)
    return outs


def _regroup_rows(
    session: SessionDep, tenant_id: str, item_ids: list[str], category: ItemCategory | None
) -> None:
    """Move the products behind these rows to `category`. Codes are untouched."""
    pids: list[str] = []
    for k in range(0, len(item_ids), 500):
        pids += [
            pid
            for (pid,) in session.execute(
                select(SupplierCatalogItem.product_id).where(
                    SupplierCatalogItem.id.in_(item_ids[k : k + 500]),
                    SupplierCatalogItem.product_id.is_not(None),
                )
            )
            if pid
        ]
    prods: list[CatalogProduct] = []
    for k in range(0, len(pids), 500):
        prods += list(
            session.scalars(
                select(CatalogProduct)
                .where(CatalogProduct.id.in_(list(dict.fromkeys(pids))[k : k + 500]))
                .order_by(CatalogProduct.code)
            )
        )
    products_svc.regroup(session, prods, category)
    # rows with no product (should not happen) still get the group
    cat_id = category.id if category else None
    session.execute(
        update(SupplierCatalogItem)
        .where(
            SupplierCatalogItem.id.in_(item_ids), SupplierCatalogItem.product_id.is_(None)
        )
        .values(category_id=cat_id, suggested_group=None)
    )


def _apply_group(
    session: SessionDep, tenant_id: str, values: dict[str, Any], group_name: str | None
) -> None:
    cleaned = clean_group_name(group_name)
    if cleaned is None:
        values["category_id"] = None
    else:
        values["category_id"] = get_or_create_category(session, tenant_id, cleaned).id
    values["suggested_group"] = None


@router.patch("/{catalog_id}/items/bulk", response_model=BulkResult)
def bulk_patch_items(
    catalog_id: str, body: BulkPatch, session: SessionDep, user: CatalogWriteUser
) -> BulkResult:
    _get_catalog(session, user.tenant_id, catalog_id)
    if (body.ids is None) == (body.filter is None):
        raise HTTPException(status_code=422, detail="Send either ids or a filter, not both.")

    C = SupplierCatalogItem
    values: dict[str, Any] = {}
    regroup_to: ItemCategory | None = None
    regroup = "group_name" in body.changes.model_fields_set
    if regroup:
        _apply_group(session, user.tenant_id, values, body.changes.group_name)
        if values["category_id"]:
            regroup_to = session.get(ItemCategory, values["category_id"])
    if body.changes.included is not None:
        values["included"] = body.changes.included
    reprice_needed = "item_margin_pct" in body.changes.model_fields_set
    if reprice_needed:
        values["item_margin_pct"] = body.changes.item_margin_pct
    if not values:
        raise HTTPException(status_code=422, detail="Nothing to change.")

    clauses = [C.tenant_id == user.tenant_id, C.catalog_id == catalog_id]
    if body.ids is not None:
        clauses.append(C.id.in_(body.ids))
    else:
        assert body.filter is not None
        clauses.extend(_filter_clauses(body.filter))

    target_ids = (
        list(session.scalars(select(C.id).where(*clauses))) if reprice_needed or regroup else []
    )
    if regroup:
        _regroup_rows(session, user.tenant_id, target_ids, regroup_to)
        values.pop("category_id", None)  # the products carry the group (and re-issued codes)
        values.pop("suggested_group", None)
    result = session.execute(
        update(C).where(*clauses).values(**values).execution_options(synchronize_session=False)
    )
    if reprice_needed and target_ids:
        reprice(session, _get_catalog(session, user.tenant_id, catalog_id), target_ids)
    return BulkResult(updated=result.rowcount or 0)  # type: ignore[attr-defined]


@router.patch("/{catalog_id}/items/{item_id}", response_model=CatalogItemOut)
def patch_item(
    catalog_id: str,
    item_id: str,
    body: CatalogItemPatch,
    session: SessionDep,
    user: CatalogWriteUser,
) -> CatalogItemOut:
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    item = _get_item(session, user.tenant_id, catalog_id, item_id)
    data = body.model_dump(exclude_unset=True)
    expected = data.pop("expected_updated_at", None)
    if expected is not None and _changed_since(item.updated_at, expected):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Someone else changed this row. It has been reloaded: check it and try again.",
        )

    if data.get("display_name") is not None:
        item.display_name = data["display_name"].strip()
        own = session.get(CatalogProduct, item.product_id) if item.product_id else None
        if own is not None:  # remembered: the next PDF with this product shows the edited name
            products_svc.rename_product(own, item.display_name)
    for field in ("brand", "size_text"):
        if field in data:
            setattr(item, field, (data[field] or "").strip() or None)
    if data.get("pack_qty") is not None:
        item.pack_qty = data["pack_qty"]
    if data.get("photo_ok"):
        item.image_flag = None
    if data.get("included") is not None:
        item.included = data["included"]
    if "group_name" in data:
        values: dict[str, Any] = {}
        _apply_group(session, user.tenant_id, values, data["group_name"])
        target = session.get(ItemCategory, values["category_id"]) if values["category_id"] else None
        _regroup_rows(session, user.tenant_id, [item.id], target)
        session.refresh(item)
    if "item_margin_pct" in data:
        item.item_margin_pct = data["item_margin_pct"]  # None clears it
    if data.get("cost_price") is not None:
        item.cost_price = data["cost_price"]
    if data.get("cost_price") is not None or "item_margin_pct" in data:
        item.sell_price = sell_price(
            item.cost_price,
            effective_margin(item.item_margin_pct, cat.bulk_margin_pct),
            cat.rounding_step,
        )
    session.flush()
    prods, _ = _product_maps(session, [item])
    out = _item_out(catalog_id, item, _category_names(session, user.tenant_id), prods)
    _price_notes(session, user.tenant_id, catalog_id, [(out, item)])
    return out


@router.get("/{catalog_id}/groups", response_model=list[GroupSummary])
def list_groups(catalog_id: str, session: SessionDep, user: CatalogUser) -> list[GroupSummary]:
    _get_catalog(session, user.tenant_id, catalog_id)
    C = SupplierCatalogItem
    rows = session.execute(
        select(
            C.category_id,
            func.count(),
            func.sum(case((C.included.is_(True), 1), else_=0)),
        )
        .where(C.tenant_id == user.tenant_id, C.catalog_id == catalog_id)
        .group_by(C.category_id)
    ).all()
    cats = _category_names(session, user.tenant_id)
    out = [
        GroupSummary(
            category_id=cid,
            name=cats.get(cid, "") if cid else "No group",
            item_count=int(n),
            included_count=int(inc or 0),
        )
        for cid, n, inc in rows
    ]
    # named groups A-Z, "No group" last
    out.sort(key=lambda g: (g.category_id is None, g.name.lower()))
    return out


def _apply_link(
    session: SessionDep, tenant_id: str, item: SupplierCatalogItem, product_id: str
) -> None:
    """This row is the same product as an existing one, so it takes that product's code, group
    and name. Only while this row's own product is provisional and used by this row alone.
    Raises 404 / 409 (nothing changed) when it cannot."""
    target = session.scalar(
        select(CatalogProduct).where(
            CatalogProduct.id == product_id, CatalogProduct.tenant_id == tenant_id
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if item.product_id != target.id:
        twin = session.scalar(
            select(SupplierCatalogItem.id).where(
                SupplierCatalogItem.catalog_id == item.catalog_id,
                SupplierCatalogItem.id != item.id,
                SupplierCatalogItem.code == target.code,
            )
        )
        if twin is not None:
            raise HTTPException(
                status_code=409,
                detail="Another row in this catalog already is that product, so this one "
                "cannot take its code.",
            )
    own = session.get(CatalogProduct, item.product_id) if item.product_id else None
    if own is not None and own.id != target.id:
        shared = session.scalar(
            select(func.count())
            .select_from(SupplierCatalogItem)
            .where(SupplierCatalogItem.product_id == own.id)
        )
        if own.item_id or (shared or 0) > 1:
            raise HTTPException(
                status_code=409,
                detail="This row's code is already in use, so it can't be merged "
                "into another product.",
            )
        if target.supplier_party_id is None and own.supplier_party_id is not None:
            # the existing product learns this supplier's code, so re-imports find it
            target.supplier_party_id = own.supplier_party_id
            target.supplier_code = own.supplier_code
            own.supplier_party_id = None
            own.supplier_code = f"{own.supplier_code}~merged~{own.id[:8]}"
            session.flush()
    item.product_id = target.id
    item.code = target.code
    item.display_name = target.display_name
    item.category_id = target.category_id
    item.suggested_group = None
    item.suggested_product_id = None
    session.flush()
    if own is not None and own.id != target.id:
        # what this product was quoted before now belongs to the product it merged into
        session.execute(
            update(SupplierPricePoint)
            .where(SupplierPricePoint.product_id == own.id)
            .values(product_id=target.id)
        )
        session.delete(own)
    session.flush()


@router.post("/{catalog_id}/items/{item_id}/link-product", response_model=CatalogItemOut)
def link_product(
    catalog_id: str,
    item_id: str,
    body: LinkProductIn,
    session: SessionDep,
    user: CatalogWriteUser,
) -> CatalogItemOut:
    """Accept a suggestion for one row."""
    _get_catalog(session, user.tenant_id, catalog_id)
    item = _get_item(session, user.tenant_id, catalog_id, item_id)
    _apply_link(session, user.tenant_id, item, body.product_id)
    prods, _ = _product_maps(session, [item])
    out = _item_out(catalog_id, item, _category_names(session, user.tenant_id), prods)
    _price_notes(session, user.tenant_id, catalog_id, [(out, item)])
    return out


@router.delete("/{catalog_id}/items/{item_id}/suggestion", response_model=CatalogItemOut)
def dismiss_suggestion(
    catalog_id: str, item_id: str, session: SessionDep, user: CatalogWriteUser
) -> CatalogItemOut:
    _get_catalog(session, user.tenant_id, catalog_id)
    item = _get_item(session, user.tenant_id, catalog_id, item_id)
    item.suggested_product_id = None
    session.flush()
    prods, _ = _product_maps(session, [item])
    out = _item_out(catalog_id, item, _category_names(session, user.tenant_id), prods)
    _price_notes(session, user.tenant_id, catalog_id, [(out, item)])
    return out


# --------------------------------------------------------------------------
# photos (signed URL, no bearer header: <img> cannot send one)
# --------------------------------------------------------------------------

_IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


@router.get("/{catalog_id}/items/{item_id}/image")
def item_image(
    catalog_id: str,
    item_id: str,
    session: SessionDep,
    storage: StorageDep,
    e: int,
    s: str,
) -> Response:
    if not image_url.verify(item_id, e, s):
        raise HTTPException(status_code=403, detail="Link expired")
    key = session.scalar(
        select(SupplierCatalogItem.image_key).where(
            SupplierCatalogItem.id == item_id, SupplierCatalogItem.catalog_id == catalog_id
        )
    )
    if not key:
        raise HTTPException(status_code=404, detail="No image")
    try:
        data = storage.get(key)
    except Exception as exc:  # noqa: BLE001 - missing object, storage hiccup
        raise HTTPException(status_code=404, detail="Image not found") from exc
    media = _IMAGE_TYPES.get(key.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return Response(
        content=data,
        media_type=media,
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --------------------------------------------------------------------------
# barcode labels
# --------------------------------------------------------------------------


def _label_rows(
    session: SessionDep,
    tenant_id: str,
    catalog_id: str,
    body: LabelRequest,
) -> list[tuple[str, str, str, Any]]:
    """(id, code, display_name, sell_price) of the chosen items, in catalog order."""
    chosen = [body.ids is not None, body.filter is not None, body.all_included]
    if sum(chosen) != 1:
        raise HTTPException(
            status_code=422, detail="Choose exactly one of: ids, filter, all_included."
        )
    C = SupplierCatalogItem
    stmt = select(C.id, C.code, C.display_name, C.sell_price).where(
        C.tenant_id == tenant_id, C.catalog_id == catalog_id
    )
    if body.ids is not None:
        stmt = stmt.where(C.id.in_(body.ids))
    elif body.filter is not None:
        stmt = stmt.where(*_filter_clauses(body.filter))
    else:
        stmt = stmt.where(C.included.is_(True))
    rows = session.execute(stmt.order_by(C.page_no, C.position, C.id)).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _label_opts(body: LabelRequest) -> labels_svc.LabelOptions:
    return labels_svc.LabelOptions(
        show_name=body.show_name,
        show_code=body.show_code,
        show_price=body.show_price,
        copies=body.copies,
        start_at=body.start_at,
    )


def _file_slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:40] or "catalog"


def _job_out(job: CatalogOutputJob) -> OutputJobOut:
    return OutputJobOut(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.total,
        page_count=job.page_count,
        scan_warning=job.scan_warning,
        error=job.error,
        result_id=(job.params_json or {}).get("customer_catalog_id"),
    )


@router.post(
    "/{catalog_id}/labels",
    response_model=None,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The label PDF."},
        202: {"model": OutputJobOut, "description": "Too big to build now; poll the job."},
    },
)
def make_labels(
    catalog_id: str,
    body: LabelRequest,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
) -> Response:
    """Barcode labels for the chosen items. Up to `SYNC_LABEL_LIMIT` labels come back as the
    PDF itself; more are built in the background and returned as a job to poll."""
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    rows = _label_rows(session, user.tenant_id, catalog_id, body)
    if not rows:
        raise HTTPException(status_code=422, detail="No items to print.")
    opts = _label_opts(body)
    n_labels = len(rows) * opts.copies
    if n_labels > output_jobs.MAX_LABELS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {n_labels} labels. The limit is {output_jobs.MAX_LABELS} per print: "
            "select fewer items or fewer copies.",
        )
    pages = output_jobs.pages_for(len(rows), body.preset, opts)

    if n_labels > output_jobs.SYNC_LABEL_LIMIT:
        output_jobs.prune_old_jobs(session, cat, storage)
        job = output_jobs.create_labels_job(
            session,
            catalog=cat,
            user_id=user.id,
            item_ids=[r[0] for r in rows],
            preset=body.preset,
            opts=opts,
            pages=pages,
        )
        session.commit()  # the background task reads this row from its own session
        background.add_task(output_jobs.run_labels_job, job.id, storage)
        return JSONResponse(status_code=202, content=_job_out(job).model_dump(mode="json"))

    items = [
        labels_svc.LabelItem(code=r[1], name=r[2], price=r[3]) for r in rows
    ]
    pdf, scannable = labels_svc.render_pdf(items, body.preset, opts, title=f"Labels {cat.title}")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="labels-{_file_slug(cat.title)}.pdf"',
            "X-Label-Count": str(n_labels),
            "X-Page-Count": str(pages),
            "X-Scan-Warning": "0" if scannable else "1",
        },
    )


@router.post("/{catalog_id}/labels/preview")
def preview_label(
    catalog_id: str, body: LabelRequest, session: SessionDep, user: CatalogUser
) -> Response:
    """The first chosen item's label as a PNG, at true proportion."""
    _get_catalog(session, user.tenant_id, catalog_id)
    rows = _label_rows(session, user.tenant_id, catalog_id, body)
    if not rows:
        raise HTTPException(status_code=422, detail="No items to preview.")
    first = rows[0]
    png = labels_svc.render_preview_png(
        labels_svc.LabelItem(code=first[1], name=first[2], price=first[3]),
        body.preset,
        _label_opts(body),
    )
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


def _get_job(
    session: SessionDep, tenant_id: str, catalog_id: str, job_id: str
) -> CatalogOutputJob:
    job = session.scalar(
        select(CatalogOutputJob).where(
            CatalogOutputJob.id == job_id,
            CatalogOutputJob.catalog_id == catalog_id,
            CatalogOutputJob.tenant_id == tenant_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return output_jobs.refresh_status(session, job)


@router.get("/{catalog_id}/outputs/{job_id}", response_model=OutputJobOut)
def get_output_job(
    catalog_id: str, job_id: str, session: SessionDep, user: CatalogUser
) -> OutputJobOut:
    return _job_out(_get_job(session, user.tenant_id, catalog_id, job_id))


@router.get("/{catalog_id}/outputs/{job_id}/file")
def get_output_file(
    catalog_id: str, job_id: str, session: SessionDep, user: CatalogUser, storage: StorageDep
) -> Response:
    job = _get_job(session, user.tenant_id, catalog_id, job_id)
    if job.status != "done" or not job.result_key:
        raise HTTPException(status_code=409, detail="That file is not ready yet.")
    try:
        data = storage.get(job.result_key)
    except Exception as exc:  # noqa: BLE001 - expired / removed object
        raise HTTPException(status_code=404, detail="That file is no longer available.") from exc
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="labels-{_file_slug(cat.title)}.pdf"'
        },
    )

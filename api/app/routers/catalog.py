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
    CustomerCatalog,
    ItemCategory,
    SupplierCatalog,
    SupplierCatalogItem,
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
    CustomerCatalogOut,
    CustomerCatalogRequest,
    GroupSummary,
    ItemFilter,
    LabelRequest,
    OutputJobOut,
)
from app.services.catalog import catalog_pdf as pdf_svc
from app.services.catalog import customer_catalogs as cc_svc
from app.services.catalog import image_url, output_jobs
from app.services.catalog import labels as labels_svc
from app.services.catalog.barcode import modules as barcode_modules
from app.services.catalog.codes import normalize_prefix
from app.services.catalog.extract_grid import NotACatalog
from app.services.catalog.groups import clean_group_name, get_or_create_category
from app.services.catalog.importer import import_catalog
from app.services.catalog.pricing import effective_multiplier, sell_price
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
def list_catalogs(session: SessionDep, user: CatalogUser) -> list[SupplierCatalog]:
    return list(
        session.scalars(
            select(SupplierCatalog)
            .where(SupplierCatalog.tenant_id == user.tenant_id)
            .order_by(SupplierCatalog.created_at.desc())
        )
    )


@router.post("", response_model=CatalogUploadOut, status_code=status.HTTP_201_CREATED)
def upload_catalog(
    response: Response,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    code_prefix: Annotated[str | None, Form()] = None,
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
    if code_prefix and code_prefix.strip() and normalize_prefix(code_prefix) is None:
        raise HTTPException(status_code=422, detail="Code prefix must be 2 to 8 letters or digits.")

    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    try:
        result = import_catalog(
            session,
            storage,
            tenant=tenant,
            user_id=user.id,
            filename=file.filename or "catalog.pdf",
            data=data,
            title=title,
            code_prefix=code_prefix,
        )
    except NotACatalog as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    out = CatalogUploadOut.model_validate(result.catalog, from_attributes=True)
    out.already_exists = not result.created
    out.skipped_cells = result.skipped_cells
    out.warnings = result.warnings[:50]
    return out


def _detail(session: SessionDep, cat: SupplierCatalog) -> CatalogDetail:
    n = session.scalar(
        select(func.count())
        .select_from(SupplierCatalogItem)
        .where(
            SupplierCatalogItem.catalog_id == cat.id,
            SupplierCatalogItem.multiplier_override.is_not(None),
        )
    )
    out = CatalogDetail.model_validate(cat, from_attributes=True)
    out.override_count = int(n or 0)
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
    pricing_changed = False
    if body.multiplier is not None and body.multiplier != cat.multiplier:
        cat.multiplier = body.multiplier
        pricing_changed = True
    if body.rounding_step is not None and body.rounding_step != cat.rounding_step:
        cat.rounding_step = body.rounding_step
        pricing_changed = True
    session.flush()
    if pricing_changed:
        reprice(session, cat)
        cc_svc.mark_stale(session, cat.id)
    return _detail(session, cat)


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
    for done in session.scalars(
        select(CustomerCatalog).where(CustomerCatalog.catalog_id == cat.id)
    ):
        keys.add(done.pdf_key)
        session.delete(done)
    session.delete(cat)
    session.flush()
    # leftover objects are harmless; the rows are already gone
    with contextlib.suppress(Exception):
        storage.delete_many(keys)


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------


# Item fields whose change makes an existing customer catalog out of date.
_CUSTOMER_FIELDS = {
    "display_name",
    "pack_qty",
    "cost_price",
    "included",
    "group_name",
    "multiplier_override",
}


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
    if f.has_override is True:
        out.append(C.multiplier_override.is_not(None))
    elif f.has_override is False:
        out.append(C.multiplier_override.is_(None))
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


def _item_out(catalog_id: str, it: SupplierCatalogItem, cats: dict[str, str]) -> CatalogItemOut:
    return CatalogItemOut(
        id=it.id,
        page_no=it.page_no,
        position=it.position,
        code=it.code,
        supplier_code=it.supplier_code,
        display_name=it.display_name,
        name_raw=it.name_raw,
        brand=it.brand,
        size_text=it.size_text,
        pack_qty=it.pack_qty,
        carton_qty=it.carton_qty,
        cost_price=it.cost_price,
        multiplier_override=it.multiplier_override,
        sell_price=it.sell_price,
        category_id=it.category_id,
        category_name=cats.get(it.category_id) if it.category_id else None,
        suggested_group=it.suggested_group,
        included=it.included,
        image_url=(
            f"/api/supplier-catalogs/{catalog_id}/items/{it.id}/image"
            f"?{image_url.sign_query(it.id)}"
            if it.image_key
            else None
        ),
        barcode=_barcode(it.code),
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
    has_override: bool | None = None,
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
        has_override=has_override,
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
    return [_item_out(catalog_id, it, cats) for it in page]


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
    if "group_name" in body.changes.model_fields_set:
        _apply_group(session, user.tenant_id, values, body.changes.group_name)
    if body.changes.included is not None:
        values["included"] = body.changes.included
    reprice_needed = "multiplier_override" in body.changes.model_fields_set
    if reprice_needed:
        values["multiplier_override"] = body.changes.multiplier_override
    if not values:
        raise HTTPException(status_code=422, detail="Nothing to change.")

    clauses = [C.tenant_id == user.tenant_id, C.catalog_id == catalog_id]
    if body.ids is not None:
        clauses.append(C.id.in_(body.ids))
    else:
        assert body.filter is not None
        clauses.extend(_filter_clauses(body.filter))

    target_ids = list(session.scalars(select(C.id).where(*clauses))) if reprice_needed else []
    result = session.execute(
        update(C).where(*clauses).values(**values).execution_options(synchronize_session=False)
    )
    if reprice_needed and target_ids:
        reprice(session, _get_catalog(session, user.tenant_id, catalog_id), target_ids)
    if result.rowcount:  # type: ignore[attr-defined]
        cc_svc.mark_stale(session, catalog_id)
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

    if data.get("display_name") is not None:
        item.display_name = data["display_name"].strip()
    for field in ("brand", "size_text"):
        if field in data:
            setattr(item, field, (data[field] or "").strip() or None)
    if data.get("pack_qty") is not None:
        item.pack_qty = data["pack_qty"]
    if data.get("included") is not None:
        item.included = data["included"]
    if "group_name" in data:
        values: dict[str, Any] = {}
        _apply_group(session, user.tenant_id, values, data["group_name"])
        item.category_id = values["category_id"]
        item.suggested_group = None
    if "multiplier_override" in data:
        item.multiplier_override = data["multiplier_override"]  # None clears it
    if data.get("cost_price") is not None:
        item.cost_price = data["cost_price"]
    if data.get("cost_price") is not None or "multiplier_override" in data:
        item.sell_price = sell_price(
            item.cost_price,
            effective_multiplier(item.multiplier_override, cat.multiplier),
            cat.rounding_step,
        )
    if data.keys() & _CUSTOMER_FIELDS:
        cc_svc.mark_stale(session, catalog_id)
    session.flush()
    return _item_out(catalog_id, item, _category_names(session, user.tenant_id))


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
    body: LabelRequest | CustomerCatalogRequest,
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


# --------------------------------------------------------------------------
# customer catalogs
# --------------------------------------------------------------------------


def _cc_out(row: CustomerCatalog) -> CustomerCatalogOut:
    return CustomerCatalogOut(
        id=row.id,
        version=row.version,
        title=row.title,
        options=row.options_json or {},
        item_count=row.item_count,
        page_count=row.page_count,
        byte_size=row.byte_size,
        stale=row.stale,
        created_at=row.created_at,
    )


def _pdf_options(body: CustomerCatalogRequest) -> pdf_svc.CatalogPdfOptions:
    return pdf_svc.CatalogPdfOptions(
        columns=body.columns,
        group_by=body.group_by,
        show_code=body.show_code,
        price_basis=body.price_basis,
        contents=body.contents,
    )


def _get_customer_catalog(
    session: SessionDep, tenant_id: str, catalog_id: str, cc_id: str
) -> CustomerCatalog:
    row = session.scalar(
        select(CustomerCatalog).where(
            CustomerCatalog.id == cc_id,
            CustomerCatalog.catalog_id == catalog_id,
            CustomerCatalog.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Customer catalog not found")
    return row


@router.post(
    "/{catalog_id}/customer-catalogs",
    response_model=None,
    responses={
        201: {"model": CustomerCatalogOut, "description": "Built; the new version."},
        202: {"model": OutputJobOut, "description": "Too big to build now; poll the job."},
    },
)
def create_customer_catalog(
    catalog_id: str,
    body: CustomerCatalogRequest,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
) -> Response:
    """Make a customer catalog PDF (your prices only). Up to `SYNC_ITEM_LIMIT` items are
    built in the request and returned as the new version; more run in the background."""
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    rows = _label_rows(session, user.tenant_id, catalog_id, body)
    if not rows:
        raise HTTPException(status_code=422, detail="No items to include.")
    if len(rows) > cc_svc.MAX_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {len(rows)} items. The limit is {cc_svc.MAX_ITEMS} per catalog: "
            "select fewer items.",
        )
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    title = (body.title or cat.title).strip()
    opts = _pdf_options(body)
    ids = [r[0] for r in rows]

    if len(ids) > cc_svc.SYNC_ITEM_LIMIT:
        job = cc_svc.create_job(
            session, catalog=cat, user_id=user.id, item_ids=ids, title=title, opts=opts
        )
        session.commit()  # the background task reads this row from its own session
        background.add_task(cc_svc.run_catalog_pdf_job, job.id, storage)
        return JSONResponse(status_code=202, content=_job_out(job).model_dump(mode="json"))

    entries = cc_svc.load_entries(session, storage, user.tenant_id, catalog_id, ids)
    pdf, pages = pdf_svc.render_pdf(
        entries, opts, cc_svc.brand_for(tenant, title), cc_svc.today()
    )
    row = cc_svc.store(
        session,
        storage,
        catalog=cat,
        user_id=user.id,
        title=title,
        opts=opts,
        pdf=pdf,
        item_count=len(entries),
        page_count=pages,
    )
    return JSONResponse(status_code=201, content=_cc_out(row).model_dump(mode="json"))


@router.post("/{catalog_id}/customer-catalogs/preview")
def preview_customer_catalog(
    catalog_id: str, body: CustomerCatalogRequest, session: SessionDep, user: CatalogUser,
    storage: StorageDep,
) -> Response:
    """The first product page as a PNG, from the first few chosen items."""
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    rows = _label_rows(session, user.tenant_id, catalog_id, body)
    if not rows:
        raise HTTPException(status_code=422, detail="No items to preview.")
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    entries = cc_svc.load_entries(
        session, storage, user.tenant_id, catalog_id, [r[0] for r in rows[:60]]
    )
    png = pdf_svc.render_preview_png(
        entries,
        _pdf_options(body),
        cc_svc.brand_for(tenant, (body.title or cat.title).strip()),
        cc_svc.today(),
    )
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{catalog_id}/customer-catalogs", response_model=list[CustomerCatalogOut])
def list_customer_catalogs(
    catalog_id: str, session: SessionDep, user: CatalogUser
) -> list[CustomerCatalogOut]:
    _get_catalog(session, user.tenant_id, catalog_id)
    rows = session.scalars(
        select(CustomerCatalog)
        .where(
            CustomerCatalog.catalog_id == catalog_id,
            CustomerCatalog.tenant_id == user.tenant_id,
        )
        .order_by(CustomerCatalog.version.desc())
    )
    return [_cc_out(r) for r in rows]


@router.get("/{catalog_id}/customer-catalogs/{cc_id}/file")
def get_customer_catalog_file(
    catalog_id: str, cc_id: str, session: SessionDep, user: CatalogUser, storage: StorageDep
) -> Response:
    row = _get_customer_catalog(session, user.tenant_id, catalog_id, cc_id)
    try:
        data = storage.get(row.pdf_key)
    except Exception as exc:  # noqa: BLE001 - removed object
        raise HTTPException(status_code=404, detail="That file is no longer available.") from exc
    name = f"catalog-{_file_slug(row.title)}-v{row.version}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete(
    "/{catalog_id}/customer-catalogs/{cc_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_customer_catalog(
    catalog_id: str, cc_id: str, session: SessionDep, user: CatalogWriteUser, storage: StorageDep
) -> None:
    row = _get_customer_catalog(session, user.tenant_id, catalog_id, cc_id)
    key = row.pdf_key
    session.delete(row)
    session.flush()
    with contextlib.suppress(Exception):
        storage.delete(key)

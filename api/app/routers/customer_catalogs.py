"""Customer catalogs: made from items, firm-level.

Same gate as the rest of the catalog module (`ext_supplier_catalog`; owner and manager write,
viewers read). Start from an Items selection: check it (what goes in, what is left out and why,
which photos are missing), then create. Up to `SYNC_ITEM_LIMIT` items are built in the request;
more run in the background and are polled. A rebuild repeats the stored selection as the next
version, so a catalog defined by a filter stays current.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.deps import SessionDep
from app.models import CatalogOutputJob, CustomerCatalog, Tenant
from app.routers.catalog import CatalogUser, CatalogWriteUser
from app.schemas_catalog import OutputJobOut
from app.schemas_customer_catalog import (
    CatalogCheckOut,
    CatalogCreate,
    CatalogRebuild,
    CatalogRequest,
    CustomerCatalogOut,
    MissingPhoto,
)
from app.services import audit
from app.services.catalog import catalog_pdf as pdf_svc
from app.services.catalog import item_catalogs as svc
from app.services.catalog import output_jobs
from app.services.catalog.storage import CatalogStorage, get_storage

router = APIRouter(prefix="/api/customer-catalogs", tags=["customer-catalogs"])
StorageDep = Annotated[CatalogStorage, Depends(get_storage)]


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:40] or "catalog"


def _tenant(session: SessionDep, tenant_id: str) -> Tenant:
    t = session.get(Tenant, tenant_id)
    assert t is not None
    return t


def _out(row: CustomerCatalog, *, stale: bool | None, latest: bool) -> CustomerCatalogOut:
    return CustomerCatalogOut(
        id=row.id,
        series_id=row.series_id,
        version=row.version,
        title=row.title,
        options=row.options_json or {},
        selection_kind=svc.selection_kind(row.selection_json),  # type: ignore[arg-type]
        item_count=row.item_count,
        page_count=row.page_count,
        byte_size=row.byte_size,
        stale=stale,
        latest=latest,
        created_at=row.created_at,
    )


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


def _get(session: SessionDep, tenant_id: str, cc_id: str) -> CustomerCatalog:
    row = session.scalar(
        select(CustomerCatalog).where(
            CustomerCatalog.id == cc_id, CustomerCatalog.tenant_id == tenant_id
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Customer catalog not found")
    return row


def _planned(
    session: SessionDep, tenant_id: str, body: CatalogRequest
) -> tuple[svc.Plan, list[str]]:
    ids = svc.resolve_selection(session, tenant_id, body.selection)
    if not ids:
        raise HTTPException(status_code=422, detail="No items selected.")
    return svc.plan(session, tenant_id, ids, body), ids


def _check_out(p: svc.Plan) -> CatalogCheckOut:
    return CatalogCheckOut(
        selected=p.selected,
        included=len(p.items),
        left_out_out_of_stock=p.left_out["out_of_stock"],
        left_out_discontinued=p.left_out["discontinued"],
        left_out_expected=p.left_out["expected"],
        no_price=p.no_price,
        missing_photos=len(p.missing),
        missing_photo_items=[
            MissingPhoto(item_id=it.id, name=it.name, code=it.sku or it.barcode)
            for it in p.missing[:100]
        ],
    )


def _start(
    background: BackgroundTasks,
    session: SessionDep,
    storage: CatalogStorage,
    user_id: str,
    tenant: Tenant,
    *,
    series_id: str,
    title: str,
    body: CatalogRequest,
    plan: svc.Plan,
    missing_photos: str,
) -> Response:
    if not plan.items:
        raise HTTPException(
            status_code=422,
            detail="Nothing to include: the selected items are out of stock, discontinued or have "
            "no selling price.",
        )
    if len(plan.items) > svc.MAX_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {len(plan.items)} items. The limit is {svc.MAX_ITEMS} per catalog: "
            "select fewer items.",
        )
    if missing_photos == "block" and plan.missing:
        raise HTTPException(
            status_code=422,
            detail=f"{len(plan.missing)} items have no photo. Add photos, or build with a "
            "placeholder.",
        )
    ids = [it.id for it in plan.items]
    if len(ids) > svc.SYNC_ITEM_LIMIT:
        job = svc.create_job(
            session,
            tenant_id=tenant.id,
            user_id=user_id,
            series_id=series_id,
            title=title,
            layout=body,
            selection=body.selection,
            item_ids=ids,
        )
        session.commit()  # the background task reads this row from its own session
        background.add_task(svc.run_job, job.id, storage)
        return JSONResponse(status_code=202, content=_job_out(job).model_dump(mode="json"))
    audit.record(
        session,
        tenant_id=tenant.id,
        user_id=user_id,
        entity="customer_catalog",
        entity_id=series_id,
        action="create_version",
        after={"title": title, "items": len(ids)},
    )
    row = svc.build(
        session,
        storage,
        tenant,
        user_id=user_id,
        series_id=series_id,
        title=title,
        layout=body,
        selection=body.selection,
        item_ids=ids,
    )
    return JSONResponse(
        status_code=201,
        content=_out(row, stale=False, latest=True).model_dump(mode="json"),
    )


@router.post("/check", response_model=CatalogCheckOut)
def check(body: CatalogRequest, session: SessionDep, user: CatalogUser) -> CatalogCheckOut:
    """What a catalog from this selection would contain, before building it."""
    p, _ = _planned(session, user.tenant_id, body)
    return _check_out(p)


@router.post(
    "",
    response_model=None,
    responses={
        201: {"model": CustomerCatalogOut, "description": "Built; the new version."},
        202: {"model": OutputJobOut, "description": "Too big to build now; poll the job."},
    },
)
def create(
    body: CatalogCreate,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
) -> Response:
    plan, _ = _planned(session, user.tenant_id, body)
    series_id = body.series_id or str(uuid.uuid4())
    if body.series_id:
        owned = session.scalar(
            select(func.count())
            .select_from(CustomerCatalog)
            .where(
                CustomerCatalog.tenant_id == user.tenant_id,
                CustomerCatalog.series_id == body.series_id,
            )
        )
        if not owned:
            raise HTTPException(status_code=404, detail="Customer catalog not found")
    return _start(
        background,
        session,
        storage,
        user.id,
        _tenant(session, user.tenant_id),
        series_id=series_id,
        title=body.title.strip(),
        body=body,
        plan=plan,
        missing_photos=body.missing_photos,
    )


@router.post("/{cc_id}/rebuild", response_model=None)
def rebuild(
    cc_id: str,
    body: CatalogRebuild,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
) -> Response:
    """The next version of this catalog from the same selection and layout. A filter is evaluated
    again, so items added since are picked up and sold-out ones drop out."""
    prev = _get(session, user.tenant_id, cc_id)
    layout = svc.layout_from_json(prev.options_json)
    selection = svc.selection_from_json(prev.selection_json)
    req = CatalogRequest(selection=selection, **layout.model_dump())
    plan, _ = _planned(session, user.tenant_id, req)
    return _start(
        background,
        session,
        storage,
        user.id,
        _tenant(session, user.tenant_id),
        series_id=prev.series_id,
        title=(body.title or prev.title).strip(),
        body=req,
        plan=plan,
        missing_photos=body.missing_photos,
    )


@router.post("/preview")
def preview(
    body: CatalogRequest, session: SessionDep, user: CatalogUser, storage: StorageDep
) -> Response:
    """The first product page as a PNG, from the first few items that would be printed."""
    plan, _ = _planned(session, user.tenant_id, body)
    if not plan.items:
        raise HTTPException(status_code=422, detail="No items to preview.")
    entries = svc.load_entries(session, storage, user.tenant_id, plan.items[:60], body)
    png = pdf_svc.render_preview_png(
        entries,
        svc.layout_options(body),
        svc.brand_for(_tenant(session, user.tenant_id), "Preview"),
        svc.today(),
    )
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}", response_model=OutputJobOut)
def job(job_id: str, session: SessionDep, user: CatalogUser) -> OutputJobOut:
    row = session.scalar(
        select(CatalogOutputJob).where(
            CatalogOutputJob.id == job_id,
            CatalogOutputJob.tenant_id == user.tenant_id,
            CatalogOutputJob.kind == "customer_catalog",
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_out(output_jobs.refresh_status(session, row))


@router.get("", response_model=list[CustomerCatalogOut])
def list_catalogs(session: SessionDep, user: CatalogUser) -> list[CustomerCatalogOut]:
    """Newest first. Whether a version is out of date is worked out only for the latest version of
    each catalog; older versions are just older."""
    rows = list(
        session.scalars(
            select(CustomerCatalog)
            .where(CustomerCatalog.tenant_id == user.tenant_id)
            .order_by(CustomerCatalog.created_at.desc(), CustomerCatalog.version.desc())
            .limit(200)
        )
    )
    latest = {}
    for r in rows:
        if r.series_id not in latest or r.version > latest[r.series_id]:
            latest[r.series_id] = r.version
    out = []
    for r in rows:
        is_latest = latest[r.series_id] == r.version
        out.append(_out(r, stale=svc.is_stale(session, r) if is_latest else None, latest=is_latest))
    return out


@router.get("/{cc_id}/file")
def file(cc_id: str, session: SessionDep, user: CatalogUser, storage: StorageDep) -> Response:
    row = _get(session, user.tenant_id, cc_id)
    try:
        data = storage.get(row.pdf_key)
    except Exception as exc:  # noqa: BLE001 - removed object
        raise HTTPException(status_code=404, detail="That file is no longer available.") from exc
    name = f"catalog-{_slug(row.title)}-v{row.version}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("/{cc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(cc_id: str, session: SessionDep, user: CatalogWriteUser, storage: StorageDep) -> None:
    row = _get(session, user.tenant_id, cc_id)
    key = row.pdf_key
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="customer_catalog",
        entity_id=row.series_id,
        action="delete_version",
        after={"title": row.title, "version": row.version},
    )
    session.delete(row)
    session.flush()
    with contextlib.suppress(Exception):
        storage.delete(key)

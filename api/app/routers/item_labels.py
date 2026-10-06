"""Barcode labels from items (same gate as the catalog module)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import SessionDep
from app.models import CatalogOutputJob, Tenant
from app.routers.catalog import CatalogUser, CatalogWriteUser
from app.schemas_catalog import LabelOptionsIn, OutputJobOut
from app.schemas_customer_catalog import CatalogSelection
from app.services.catalog import item_labels as svc
from app.services.catalog import labels as labels_svc
from app.services.catalog import output_jobs
from app.services.catalog.storage import CatalogStorage, get_storage

router = APIRouter(prefix="/api/item-labels", tags=["item-labels"])
StorageDep = Annotated[CatalogStorage, Depends(get_storage)]


class LabelsRequest(LabelOptionsIn):
    selection: CatalogSelection
    # items with no code get the next number; off = leave them out
    assign_codes: bool = True


class LabelsCheckOut(BaseModel):
    selected: int
    printable: int
    labels: int  # printable x copies
    pages: int
    no_code: int  # printable items with no code yet
    inactive: int
    no_price: int


def _opts(body: LabelsRequest) -> labels_svc.LabelOptions:
    return labels_svc.LabelOptions(
        show_name=body.show_name,
        show_code=body.show_code,
        show_price=body.show_price,
        copies=body.copies,
        start_at=body.start_at,
    )


def _planned(session: SessionDep, tenant_id: str, body: LabelsRequest) -> svc.LabelPlan:
    ids = svc.resolve_selection(session, tenant_id, body.selection)
    if not ids:
        raise HTTPException(status_code=422, detail="No items selected.")
    return svc.plan(session, tenant_id, ids)


def _job_out(job: CatalogOutputJob) -> OutputJobOut:
    return OutputJobOut(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.total,
        page_count=job.page_count,
        scan_warning=job.scan_warning,
        error=job.error,
    )


@router.post("/check", response_model=LabelsCheckOut)
def check(body: LabelsRequest, session: SessionDep, user: CatalogUser) -> LabelsCheckOut:
    p = _planned(session, user.tenant_id, body)
    opts = _opts(body)
    n = len(p.items) if body.assign_codes else len(svc.drop_uncoded(p.items))
    return LabelsCheckOut(
        selected=p.selected,
        printable=n,
        labels=n * opts.copies,
        pages=svc.pages_for(n, body.preset, opts) if n else 0,
        no_code=p.no_code,
        inactive=p.inactive,
        no_price=p.no_price if body.show_price else 0,
    )


@router.post(
    "",
    response_model=None,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The label PDF."},
        202: {"model": OutputJobOut, "description": "Too big to build now; poll the job."},
    },
)
def make(
    body: LabelsRequest,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
) -> Response:
    p = _planned(session, user.tenant_id, body)
    if not body.assign_codes:
        p.items = svc.drop_uncoded(p.items)
    if not p.items:
        raise HTTPException(status_code=422, detail="No items to print.")
    opts = _opts(body)
    n_labels = len(p.items) * opts.copies
    if n_labels > svc.MAX_LABELS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {n_labels} labels. The limit is {svc.MAX_LABELS} per print: "
            "select fewer items or fewer copies.",
        )
    pages = svc.pages_for(len(p.items), body.preset, opts)
    if body.assign_codes:
        tenant = session.get(Tenant, user.tenant_id)
        assert tenant is not None
        svc.assign_missing_codes(session, tenant, p.items)
        session.commit()  # the codes are used from now on, whether or not the print succeeds
    if n_labels > svc.SYNC_LABEL_LIMIT:
        job = svc.create_job(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            item_ids=[it.id for it in p.items],
            preset=body.preset,
            opts=opts,
            pages=pages,
        )
        session.commit()  # the background task reads this row from its own session
        background.add_task(svc.run_job, job.id, storage)
        return JSONResponse(status_code=202, content=_job_out(job).model_dump(mode="json"))
    pdf, scannable = labels_svc.render_pdf(
        svc.label_items(p.items), body.preset, opts, title="Labels"
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="labels.pdf"',
            "X-Label-Count": str(n_labels),
            "X-Page-Count": str(pages),
            "X-Scan-Warning": "0" if scannable else "1",
        },
    )


@router.post("/preview")
def preview(body: LabelsRequest, session: SessionDep, user: CatalogUser) -> Response:
    """The first label as a PNG at true proportion."""
    p = _planned(session, user.tenant_id, body)
    if not p.items:
        raise HTTPException(status_code=422, detail="No items to preview.")
    first = svc.label_items(p.items[:1])[0]
    if not first.code:  # not issued yet: show a sample so the barcode renders
        first = labels_svc.LabelItem(code="100000", name=first.name, price=first.price)
    png = labels_svc.render_preview_png(first, body.preset, _opts(body))
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


def _job(session: SessionDep, tenant_id: str, job_id: str) -> CatalogOutputJob:
    row = session.scalar(
        select(CatalogOutputJob).where(
            CatalogOutputJob.id == job_id,
            CatalogOutputJob.tenant_id == tenant_id,
            CatalogOutputJob.kind == "item_labels",
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return output_jobs.refresh_status(session, row)


@router.get("/jobs/{job_id}", response_model=OutputJobOut)
def job(job_id: str, session: SessionDep, user: CatalogUser) -> OutputJobOut:
    return _job_out(_job(session, user.tenant_id, job_id))


@router.get("/jobs/{job_id}/file")
def job_file(job_id: str, session: SessionDep, user: CatalogUser, storage: StorageDep) -> Response:
    row = _job(session, user.tenant_id, job_id)
    if row.status != "done" or not row.result_key:
        raise HTTPException(status_code=409, detail="That file is not ready yet.")
    try:
        data = storage.get(row.result_key)
    except Exception as exc:  # noqa: BLE001 - expired / removed object
        raise HTTPException(status_code=404, detail="That file is no longer available.") from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="labels.pdf"'},
    )

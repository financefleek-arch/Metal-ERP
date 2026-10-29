"""A customer PDF straight from a supplier price list (PDF in, PDF out; items are optional).

Same gate as the rest of the catalog module. Nothing here needs the products to be in the item list,
and nothing is left out because of stock: a price list says nothing about stock.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import SessionDep
from app.models import SupplierCatalogItem, Tenant
from app.routers.catalog import (
    CatalogUser,
    CatalogWriteUser,
    StorageDep,
    _get_catalog,
    _job_out,
    _label_rows,
)
from app.schemas_catalog import OutputJobOut, SelectionIn
from app.schemas_customer_catalog import CatalogLayout
from app.services.catalog import catalog_pdf, item_catalogs, output_jobs
from app.services.catalog import supplier_pdf as svc

router = APIRouter(prefix="/api/supplier-catalogs", tags=["supplier-catalog-customer-pdf"])


class CustomerPdfIn(SelectionIn, CatalogLayout):
    """Which rows (ids, filter or all included) and how the pages look."""

    title: str | None = Field(default=None, min_length=1, max_length=200)


class CustomerPdfCheckOut(BaseModel):
    selected: int
    included: int
    no_price: int
    missing_photos: int


def _rows(
    session: SessionDep, tenant_id: str, catalog_id: str, body: CustomerPdfIn
) -> list[SupplierCatalogItem]:
    ids = [r[0] for r in _label_rows(session, tenant_id, catalog_id, body)]  # type: ignore[arg-type]
    if not ids:
        raise HTTPException(status_code=422, detail="No products selected.")
    if len(ids) > svc.MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {len(ids)} products. The limit is {svc.MAX_ROWS} per PDF.",
        )
    found: dict[str, SupplierCatalogItem] = {}
    for i in range(0, len(ids), 500):
        for row in session.scalars(
            select(SupplierCatalogItem).where(SupplierCatalogItem.id.in_(ids[i : i + 500]))
        ):
            found[row.id] = row
    return [found[i] for i in ids if i in found]


def _layout(body: CustomerPdfIn) -> CatalogLayout:
    return CatalogLayout.model_validate(body.model_dump(include=set(CatalogLayout.model_fields)))


@router.post("/{catalog_id}/customer-pdf/check", response_model=CustomerPdfCheckOut)
def check(
    catalog_id: str, body: CustomerPdfIn, session: SessionDep, user: CatalogUser
) -> CustomerPdfCheckOut:
    _get_catalog(session, user.tenant_id, catalog_id)
    p = svc.plan(_rows(session, user.tenant_id, catalog_id, body))
    return CustomerPdfCheckOut(
        selected=p.selected,
        included=len(p.rows),
        no_price=p.no_price,
        missing_photos=p.missing_photos,
    )


@router.post("/{catalog_id}/customer-pdf/preview")
def preview(
    catalog_id: str,
    body: CustomerPdfIn,
    session: SessionDep,
    user: CatalogUser,
    storage: StorageDep,
) -> Response:
    """The first product page as a PNG, from the first few products that would be printed."""
    _get_catalog(session, user.tenant_id, catalog_id)
    p = svc.plan(_rows(session, user.tenant_id, catalog_id, body))
    if not p.rows:
        raise HTTPException(status_code=422, detail="No products to preview.")
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    layout = _layout(body)
    entries = svc.load_entries(session, storage, user.tenant_id, p.rows[:60])
    png = catalog_pdf.render_preview_png(
        entries,
        item_catalogs.layout_options(layout),
        item_catalogs.brand_for(tenant, "Preview"),
        item_catalogs.today(),
    )
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/{catalog_id}/customer-pdf", response_model=OutputJobOut)
def make(
    catalog_id: str,
    body: CustomerPdfIn,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: StorageDep,
) -> OutputJobOut:
    """The PDF as a job. An identical PDF built earlier is returned as it was (already `done`);
    a small one is built before this returns, a large one in the background (poll the job, then
    fetch `/outputs/{job}/file`)."""
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    p = svc.plan(_rows(session, user.tenant_id, catalog_id, body))
    if not p.rows:
        raise HTTPException(status_code=422, detail="None of those products has a price.")
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    layout = _layout(body)
    title = (body.title or cat.title).strip()
    fp = svc.fingerprint(session, tenant, p.rows, layout, title)

    built = svc.find_built(session, cat.id, fp)
    if built is not None:
        return _job_out(built)

    output_jobs.prune_old_jobs(session, cat, storage)
    job = svc.create_job(
        session,
        catalog=cat,
        user_id=user.id,
        title=title,
        layout=layout,
        row_ids=[r.id for r in p.rows],
        fp=fp,
    )
    session.commit()  # the build reads this row from its own session
    if len(p.rows) <= svc.SYNC_LIMIT:
        svc.run_job(job.id, storage)
        session.expire_all()
        session.refresh(job)
    else:
        background.add_task(svc.run_job, job.id, storage)
    return _job_out(job)

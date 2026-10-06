"""Items in a spreadsheet: export a selection, edit it in Excel, import it back by code."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Item
from app.routers.items import _normalized
from app.schemas_item import MAX_BULK_FILTER, ItemFilter
from app.services import audit
from app.services import item_sheet as svc
from app.services.item_filter import filter_stmt

router = APIRouter(prefix="/api/item-sheet", tags=["item-sheet"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportIn(BaseModel):
    """Which items: ticked ids, everything matching a filter, or neither (all items)."""

    ids: list[str] | None = None
    filter: ItemFilter | None = None

    @model_validator(mode="after")
    def _one(self) -> ExportIn:
        if self.ids is not None and self.filter is not None:
            raise ValueError("send either ids or a filter, not both")
        return self


class RowOut(BaseModel):
    row: int
    code: str
    name: str
    result: str
    detail: str | None


class ImportOut(BaseModel):
    dry_run: bool
    changed: int
    unchanged: int
    errors: int
    rows: list[RowOut]


@router.post("/export")
def export_items(body: ExportIn, user: CurrentUser, session: SessionDep) -> Response:
    if body.ids is not None:
        items = list(
            session.scalars(
                select(Item).where(Item.tenant_id == user.tenant_id, Item.id.in_(body.ids))
            )
        )
        order = {i: n for n, i in enumerate(body.ids)}
        items.sort(key=lambda it: order[it.id])
    else:
        flt = body.filter or ItemFilter()
        items = list(session.scalars(filter_stmt(session, user.tenant_id, flt)).unique().all())
        if len(items) > MAX_BULK_FILTER:
            raise HTTPException(
                status_code=422,
                detail=f"That is {len(items)} items. Narrow it to {MAX_BULK_FILTER} or fewer.",
            )
        items.sort(key=lambda it: it.name.casefold())
    items = [it for it in items if it.merged_into_id is None]
    data = svc.export_xlsx(session, user.tenant_id, items)
    return Response(
        content=data,
        media_type=XLSX,
        headers={"Content-Disposition": 'attachment; filename="items.xlsx"'},
    )


@router.post("/import", response_model=ImportOut)
def import_items(
    file: Annotated[UploadFile, File()],
    user: WriteUser,
    session: SessionDep,
    dry_run: bool = Query(default=True),
) -> ImportOut:
    """Update items from a sheet, matched by code. Dry run by default: check, then apply."""
    data = file.file.read(svc.MAX_BYTES + 1)
    try:
        rows = svc.read_rows(data, file.filename or "items.xlsx")
    except svc.SheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    res = svc.apply_rows(
        session,
        user.tenant_id,
        rows,
        dry_run=dry_run,
        normalize=lambda name: _normalized(session, user.tenant_id, name),
    )
    if dry_run:
        session.rollback()
    elif res.changed:
        audit.record(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity="item",
            entity_id=user.tenant_id,
            action="sheet_import",
            after={"file": file.filename, "changed": res.changed, "errors": res.errors},
        )
    return ImportOut(
        dry_run=res.dry_run,
        changed=res.changed,
        unchanged=res.unchanged,
        errors=res.errors,
        rows=[
            RowOut(row=r.row, code=r.code, name=r.name, result=r.result, detail=r.detail)
            for r in res.rows
        ],
    )

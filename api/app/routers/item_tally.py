"""Send items to Tally (same gate as the catalog module).

A push starts from an Items selection. The state of each item (not sent, queued, in Tally, price
to update, price differs, error) lives on the item. Pushing goes through the Tally agent in small,
sequential batches; see `services/catalog/tally_items.py` for why.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import SessionDep
from app.models import Item, Tenant
from app.routers.catalog import CatalogUser, CatalogWriteUser
from app.schemas_catalog import (
    PreflightCheckOut,
    PreflightCollisionOut,
    PreflightGroupOut,
    TallyPreflightOut,
    TallyPushIn,
    TallyRunOut,
)
from app.services import audit
from app.services.catalog import item_labels
from app.services.catalog import tally_items as ti
from app.services.item_filter import resolve_ids

router = APIRouter(prefix="/api/item-tally", tags=["item-tally"])


def _items(session: SessionDep, tenant_id: str, body: TallyPushIn) -> list[Item]:
    ids = resolve_ids(session, tenant_id, body.selection.ids, body.selection.filter)
    if not ids:
        raise HTTPException(status_code=422, detail="No items selected.")
    found: dict[str, Item] = {}
    for i in range(0, len(ids), 500):
        for it in session.scalars(
            select(Item).where(Item.tenant_id == tenant_id, Item.id.in_(ids[i : i + 500]))
        ):
            found[it.id] = it
    return [found[i] for i in ids if i in found]


def _preflight_out(pf: ti.Preflight) -> TallyPreflightOut:
    return TallyPreflightOut(
        ok=pf.ok,
        total=pf.total,
        to_push=pf.to_push,
        already_synced=pf.already_synced,
        skipped_existing=pf.skipped_existing,
        price_updates=pf.price_updates,
        inactive=pf.inactive,
        batches=pf.batches,
        root=pf.root,
        checked_at=pf.checked_at,
        checks=[
            PreflightCheckOut(code=c.code, ok=c.ok, message=c.message, blocking=c.blocking)
            for c in pf.checks
        ],
        groups=[
            PreflightGroupOut(
                category_id=g.category_id,
                our_name=g.our_name,
                tally_name=g.tally_name,
                status=g.status,
                item_count=g.item_count,
            )
            for g in pf.groups
        ],
        collisions=[
            PreflightCollisionOut(item_id=c.item_id, code=c.code, name=c.name)
            for c in pf.collisions
        ],
    )


def _run_out(run: ti.RunStatus) -> TallyRunOut:
    return TallyRunOut(
        run_id=run.run_id,
        state=run.state,
        batches=run.batches,
        batches_done=run.batches_done,
        total_items=run.total_items,
        synced=run.synced,
        error=run.error,
        updated_at=run.updated_at,
        waiting=run.waiting,
        phase=run.phase,
        started_at=run.started_at,
        eta_seconds=run.eta_seconds,
    )


@router.post("/preflight", response_model=TallyPreflightOut)
def preflight(body: TallyPushIn, session: SessionDep, user: CatalogUser) -> TallyPreflightOut:
    items = _items(session, user.tenant_id, body)
    pf, _, _ = ti.preflight(
        session,
        user.tenant_id,
        items,
        include_synced=body.include_synced,
        skip_existing=body.skip_existing,
    )
    return _preflight_out(pf)


@router.post("/push", response_model=TallyRunOut, status_code=status.HTTP_201_CREATED)
def push(body: TallyPushIn, session: SessionDep, user: CatalogWriteUser) -> TallyRunOut:
    """Send the chosen items to Tally, a batch at a time. Poll `GET /run`."""
    items = _items(session, user.tenant_id, body)
    pf, todo, _creates = ti.preflight(
        session,
        user.tenant_id,
        items,
        include_synced=body.include_synced,
        skip_existing=body.skip_existing,
    )
    if pf.blockers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=" ".join(b.message for b in pf.blockers),
        )
    if not todo:
        raise HTTPException(status_code=422, detail="There is nothing to send.")
    company = ti.get_company(session, user.tenant_id)
    assert company is not None  # preflight confirmed it
    tenant = session.get(Tenant, user.tenant_id)
    assert tenant is not None
    # the code is the part number in Tally, so every item sent has one
    item_labels.assign_missing_codes(session, tenant, [r.item for r in todo])
    ti.start_run(session, company, todo, _creates, pf.groups)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="item_tally",
        entity_id=user.tenant_id,
        action="push_items",
        after={"items": len(todo), "price_updates": pf.price_updates},
    )
    return _run_out(ti.latest_run(session, user.tenant_id))


@router.get("/run", response_model=TallyRunOut)
def run(session: SessionDep, user: CatalogUser) -> TallyRunOut:
    return _run_out(ti.latest_run(session, user.tenant_id))

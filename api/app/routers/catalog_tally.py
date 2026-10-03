"""Supplier Catalog S5: put chosen rows in the item master, then in Tally.

Same gate as the rest of the catalog module (`ext_supplier_catalog`; owner/accountant write).
Pushing goes through the Tally agent in small, sequential batches; see
`services/catalog/tally_items.py` for why.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import SessionDep
from app.models import ItemCategory, SupplierCatalogItem, TallyCompany, TallySyncJob
from app.routers.catalog import CatalogUser, CatalogWriteUser, _get_catalog, _label_rows
from app.schemas_catalog import (
    PreflightCheckOut,
    PreflightCollisionOut,
    PreflightGroupOut,
    PromoteOut,
    SelectionIn,
    TallyCheckOut,
    TallyPreflightOut,
    TallyPushIn,
    TallyRunOut,
    TallySettingsIn,
    TallySettingsOut,
)
from app.services.catalog import promote as promote_svc
from app.services.catalog import tally_items as ti
from app.services.tally.agent_health import assert_tally_reachable

router = APIRouter(prefix="/api/supplier-catalogs", tags=["supplier-catalog-tally"])


def _rows(
    session: SessionDep, tenant_id: str, catalog_id: str, body: SelectionIn
) -> list[SupplierCatalogItem]:
    ids = [r[0] for r in _label_rows(session, tenant_id, catalog_id, body)]  # type: ignore[arg-type]
    if not ids:
        raise HTTPException(status_code=422, detail="No items selected.")
    found: dict[str, SupplierCatalogItem] = {}
    for i in range(0, len(ids), 500):
        for row in session.scalars(
            select(SupplierCatalogItem).where(SupplierCatalogItem.id.in_(ids[i : i + 500]))
        ):
            found[row.id] = row
    return [found[i] for i in ids if i in found]


# --------------------------------------------------------------------------
# promote to the item master
# --------------------------------------------------------------------------


def _promote_out(total: int, plan: promote_svc.PromotePlan) -> PromoteOut:
    return PromoteOut(
        total=total,
        create=plan.create,
        link_existing=plan.link_existing,
        reuse=plan.reuse,
        already=plan.already,
        renamed=plan.renamed,
        examples=plan.examples,
    )


@router.post("/{catalog_id}/promote/preview", response_model=PromoteOut)
def promote_preview(
    catalog_id: str, body: SelectionIn, session: SessionDep, user: CatalogUser
) -> PromoteOut:
    _get_catalog(session, user.tenant_id, catalog_id)
    rows = _rows(session, user.tenant_id, catalog_id, body)
    return _promote_out(len(rows), promote_svc.preview(session, user.tenant_id, rows))


@router.post("/{catalog_id}/promote", response_model=PromoteOut)
def promote(
    catalog_id: str, body: SelectionIn, session: SessionDep, user: CatalogWriteUser
) -> PromoteOut:
    """Create (or link) one item per product. Also fixes each product's code for good."""
    _get_catalog(session, user.tenant_id, catalog_id)
    rows = _rows(session, user.tenant_id, catalog_id, body)
    return _promote_out(len(rows), promote_svc.promote(session, user.tenant_id, rows))


# --------------------------------------------------------------------------
# Tally settings + "check Tally"
# --------------------------------------------------------------------------


def _settings_out(company: TallyCompany | None) -> TallySettingsOut:
    if company is None:
        return TallySettingsOut(
            connected=False,
            company_name=None,
            stock_group_root=None,
            tally_group_create_policy="create_missing",
            stock_group_map={},
            checked_at=None,
            check_is_fresh=False,
            known_groups=[],
        )
    return TallySettingsOut(
        connected=company.shop_id is not None,
        company_name=company.company_name,
        stock_group_root=company.stock_group_root,
        tally_group_create_policy=company.tally_group_create_policy,
        stock_group_map=dict(company.stock_group_map or {}),
        checked_at=company.known_stock_at,
        check_is_fresh=ti.check_is_fresh(company),
        known_groups=sorted(
            {str(g["name"]) for g in (company.known_stock_groups or []) if g.get("name")},
            key=str.lower,
        ),
    )


@router.get("/tally/settings", response_model=TallySettingsOut)
def get_tally_settings(session: SessionDep, user: CatalogUser) -> TallySettingsOut:
    return _settings_out(ti.get_company(session, user.tenant_id))


@router.put("/tally/settings", response_model=TallySettingsOut)
def put_tally_settings(
    body: TallySettingsIn, session: SessionDep, user: CatalogWriteUser
) -> TallySettingsOut:
    company = ti.get_company(session, user.tenant_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Tally is not connected for this firm yet.")
    patch = body.model_fields_set
    if "stock_group_root" in patch:
        company.stock_group_root = (body.stock_group_root or "").strip() or None
    if body.tally_group_create_policy is not None:
        company.tally_group_create_policy = body.tally_group_create_policy
    if "stock_group_map" in patch and body.stock_group_map is not None:
        valid = set(
            session.scalars(
                select(ItemCategory.id).where(ItemCategory.tenant_id == user.tenant_id)
            )
        )
        merged = dict(company.stock_group_map or {})
        for cid, name in body.stock_group_map.items():
            if cid not in valid:
                raise HTTPException(status_code=422, detail="Unknown group.")
            clean = (name or "").strip()
            if clean:
                if len(clean) > ti.NAME_MAX:
                    raise HTTPException(
                        status_code=422, detail="That Tally group name is too long."
                    )
                merged[cid] = clean
            else:
                merged.pop(cid, None)
        company.stock_group_map = merged
    session.flush()
    return _settings_out(company)


def _check_out(job: TallySyncJob) -> TallyCheckOut:
    c = job.counts or {}
    return TallyCheckOut(
        id=job.id,
        status=job.status,
        error=job.error,
        groups=c.get("groups"),
        items=c.get("items"),
        linked=c.get("linked"),
        created_at=job.created_at,
    )


@router.post("/tally/check", response_model=TallyCheckOut, status_code=status.HTTP_201_CREATED)
def start_tally_check(session: SessionDep, user: CatalogWriteUser) -> TallyCheckOut:
    """Read Tally's stock groups and item names through the agent."""
    company = ti.get_company(session, user.tenant_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Tally is not connected for this firm yet.")
    assert_tally_reachable(session, company)
    return _check_out(ti.enqueue_stock_check(session, company))


@router.get("/tally/check", response_model=TallyCheckOut | None)
def get_tally_check(session: SessionDep, user: CatalogUser) -> TallyCheckOut | None:
    job = ti.latest_check(session, user.tenant_id)
    return _check_out(job) if job is not None else None


# --------------------------------------------------------------------------
# push
# --------------------------------------------------------------------------


def _preflight_out(pf: ti.Preflight) -> TallyPreflightOut:
    return TallyPreflightOut(
        ok=pf.ok,
        total=pf.total,
        to_push=pf.to_push,
        already_synced=pf.already_synced,
        not_promoted=pf.not_promoted,
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


@router.post("/{catalog_id}/tally/preflight", response_model=TallyPreflightOut)
def tally_preflight(
    catalog_id: str, body: TallyPushIn, session: SessionDep, user: CatalogUser
) -> TallyPreflightOut:
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    rows = _rows(session, user.tenant_id, catalog_id, body)
    pf, _, _ = ti.preflight(
        session, user.tenant_id, cat, rows, include_synced=body.include_synced
    )
    return _preflight_out(pf)


@router.post(
    "/{catalog_id}/tally/push", response_model=TallyRunOut, status_code=status.HTTP_201_CREATED
)
def tally_push(
    catalog_id: str, body: TallyPushIn, session: SessionDep, user: CatalogWriteUser
) -> TallyRunOut:
    """Send the chosen items to Tally, a batch at a time. Poll `GET .../tally/run`."""
    cat = _get_catalog(session, user.tenant_id, catalog_id)
    rows = _rows(session, user.tenant_id, catalog_id, body)
    pf, todo, creates = ti.preflight(
        session, user.tenant_id, cat, rows, include_synced=body.include_synced
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
    ti.start_run(session, company, cat, todo, creates, pf.groups)
    return _run_out(ti.latest_run(session, user.tenant_id, catalog_id))


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
    )


@router.get("/{catalog_id}/tally/run", response_model=TallyRunOut)
def tally_run(catalog_id: str, session: SessionDep, user: CatalogUser) -> TallyRunOut:
    _get_catalog(session, user.tenant_id, catalog_id)
    return _run_out(ti.latest_run(session, user.tenant_id, catalog_id))

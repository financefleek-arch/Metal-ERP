"""Supplier Catalog S5: put chosen rows in the item master; Tally settings and "check Tally".

Same gate as the rest of the catalog module (`ext_supplier_catalog`; owner/accountant write).
Sending items to Tally starts from the Items page: see `routers/item_tally.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select

from app.deps import SessionDep
from app.models import ItemCategory, SupplierCatalogItem, TallyCompany, TallySyncJob
from app.routers.catalog import CatalogUser, CatalogWriteUser, _get_catalog, _label_rows
from app.schemas_catalog import (
    PromoteIn,
    PromoteOut,
    SelectionIn,
    TallyCheckOut,
    TallySettingsIn,
    TallySettingsOut,
)
from app.services import audit
from app.services import media as media_svc
from app.services.catalog import promote as promote_svc
from app.services.catalog import supplier_defaults as defaults_svc
from app.services.catalog import tally_items as ti
from app.services.catalog.storage import CatalogStorage, get_storage
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
        created_item_ids=plan.created_ids,
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
    catalog_id: str,
    body: PromoteIn,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: Annotated[CatalogStorage, Depends(get_storage)],
) -> PromoteOut:
    """Create (or link) one item per product, then copy each catalog photo onto its item in the
    background (an item that already has a photo keeps it)."""
    _get_catalog(session, user.tenant_id, catalog_id)
    rows = _rows(session, user.tenant_id, catalog_id, body)
    plan = promote_svc.promote(session, user.tenant_id, rows)
    if body.mark_in_stock:
        defaults_svc.mark_in_stock(session, rows)
    pairs = media_svc.items_needing_photos(session, user.tenant_id, rows)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="supplier_catalog",
        entity_id=catalog_id,
        action="add_to_items",
        after={"rows": len(rows), "created": plan.create, "linked": plan.link_existing},
    )
    out = _promote_out(len(rows), plan)
    out.photos_queued = len(pairs)
    if pairs:
        session.commit()  # the background task reads these rows from its own session
        background.add_task(media_svc.copy_catalog_photos, user.tenant_id, pairs, storage)
    return out


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
            session.scalars(select(ItemCategory.id).where(ItemCategory.tenant_id == user.tenant_id))
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

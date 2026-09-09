"""Tally Connector API — operator-only, per-firm (F1a: masters-in).

Mounted at `/api/admin/firms/{firm_id}/tally/*` alongside the other
platform-admin firm-provisioning routes: a Fleek operator links a client
firm's Tally company, maps its ledgers, triggers a masters pull, and
watches the sync job. Same `require_platform_admin` gate and `firm_id`-in-
path shape as `/api/admin/firms/{firm_id}/whatsapp`.

The agent-facing callbacks (`/api/tally-agent/jobs/{id}/result` and
`/status`) live in `routers/tally_agent.py` with the other `ShopAuth`
endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.backup_storage import R2NotConfigured, get_object
from app.deps import SessionDep, require_platform_admin
from app.models import BackupShop, Invoice, TallyCompany, TallySyncJob, Tenant
from app.schemas_tally import (
    LedgerMapIn,
    TallyCompanyIn,
    TallyCompanyOut,
    TallyPushBlockersOut,
    TallySyncJobOut,
)
from app.services.tally.agent_health import assert_tally_reachable
from app.services.tally.jobs import (
    assert_no_pull_in_flight,
    assert_no_push_in_flight,
    enqueue_pull_masters,
    enqueue_push_sales,
)
from app.services.tally.push_readiness import get_tally_company, push_blockers

router = APIRouter(
    prefix="/api/admin/firms/{firm_id}/tally",
    tags=["tally"],
    dependencies=[Depends(require_platform_admin)],
)


def _firm(session: SessionDep, firm_id: str) -> Tenant:
    firm = session.get(Tenant, firm_id)
    if firm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firm not found")
    return firm


def _load_company(session: SessionDep, firm_id: str) -> TallyCompany:
    company = session.scalar(
        select(TallyCompany).where(TallyCompany.tenant_id == firm_id)
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Tally company is linked to this firm yet.",
        )
    return company


# --------------------------------------------------------------------------
# company + ledger map
# --------------------------------------------------------------------------


@router.get("/company", response_model=TallyCompanyOut)
def get_company(firm_id: str, session: SessionDep) -> TallyCompany:
    _firm(session, firm_id)
    return _load_company(session, firm_id)


@router.post("/company", response_model=TallyCompanyOut)
def upsert_company(
    firm_id: str, body: TallyCompanyIn, session: SessionDep
) -> TallyCompany:
    _firm(session, firm_id)
    # The agent is provisioned from the firm's page (POST
    # /firms/{id}/tally-shop) and there is exactly one per firm, so
    # `shop_id` is resolved here rather than picked — the request body only
    # carries the Tally company name.
    shop = session.scalar(
        select(BackupShop).where(BackupShop.tenant_id == firm_id)
    )

    company = session.scalar(
        select(TallyCompany).where(TallyCompany.tenant_id == firm_id)
    )
    if company is None:
        company = TallyCompany(
            tenant_id=firm_id,
            company_name=body.company_name,
            shop_id=shop.id if shop else None,
            ledger_map={},
        )
        session.add(company)
    else:
        company.company_name = body.company_name
        company.shop_id = shop.id if shop else None
    session.flush()
    return company


@router.put("/company/ledger-map", response_model=TallyCompanyOut)
def put_ledger_map(
    firm_id: str, body: LedgerMapIn, session: SessionDep
) -> TallyCompany:
    _firm(session, firm_id)
    company = _load_company(session, firm_id)
    patch = body.model_dump(exclude_unset=True)
    merged = dict(company.ledger_map or {})
    for k, v in patch.items():
        if v is None or v.strip() == "":
            merged.pop(k, None)
        else:
            merged[k] = v.strip()
    company.ledger_map = merged
    session.flush()
    return company


# --------------------------------------------------------------------------
# pull masters + sync jobs
# --------------------------------------------------------------------------


@router.post(
    "/pull-masters",
    response_model=TallySyncJobOut,
    status_code=status.HTTP_201_CREATED,
)
def pull_masters(firm_id: str, session: SessionDep) -> TallySyncJob:
    _firm(session, firm_id)
    company = _load_company(session, firm_id)
    assert_no_pull_in_flight(session, firm_id)
    assert_tally_reachable(session, company)
    return enqueue_pull_masters(session, company)


# --------------------------------------------------------------------------
# push a sales voucher (F1b-1)
# --------------------------------------------------------------------------


def _owned_invoice(session: SessionDep, firm_id: str, invoice_id: str) -> Invoice:
    inv = session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == firm_id)
    )
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return inv


@router.get("/invoices/{invoice_id}/push-status", response_model=TallyPushBlockersOut)
def get_push_status(firm_id: str, invoice_id: str, session: SessionDep) -> TallyPushBlockersOut:
    _firm(session, firm_id)
    invoice = _owned_invoice(session, firm_id, invoice_id)
    blockers = push_blockers(session, invoice)
    return TallyPushBlockersOut(
        pushable=not blockers,
        blockers=[{"code": b.code, "message": b.message} for b in blockers],
    )


@router.post(
    "/invoices/{invoice_id}/push",
    response_model=TallySyncJobOut,
    status_code=status.HTTP_201_CREATED,
)
def push_invoice(firm_id: str, invoice_id: str, session: SessionDep) -> TallySyncJob:
    _firm(session, firm_id)
    invoice = _owned_invoice(session, firm_id, invoice_id)
    blockers = push_blockers(session, invoice)
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(b.message for b in blockers),
        )
    company = get_tally_company(session, firm_id)
    assert company is not None  # push_blockers() already confirmed this
    assert_no_push_in_flight(session, firm_id, invoice_id)
    assert_tally_reachable(session, company)
    return enqueue_push_sales(session, company, invoice)


@router.get("/sync-jobs", response_model=list[TallySyncJobOut])
def list_sync_jobs(
    firm_id: str,
    session: SessionDep,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[TallySyncJob]:
    _firm(session, firm_id)
    return list(
        session.scalars(
            select(TallySyncJob)
            .where(TallySyncJob.tenant_id == firm_id)
            .order_by(TallySyncJob.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.get("/sync-jobs/{job_id}", response_model=TallySyncJobOut)
def get_sync_job(
    firm_id: str, job_id: str, session: SessionDep
) -> TallySyncJob:
    _firm(session, firm_id)
    job = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.id == job_id, TallySyncJob.tenant_id == firm_id
        )
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/sync-jobs/{job_id}/xml")
def download_sync_job_xml(
    firm_id: str, job_id: str, session: SessionDep
) -> Response:
    """The masters XML the agent uploaded for this job — for debugging a
    parse failure against the real file. Only a job that has an `r2_key`
    (the agent got as far as uploading).
    """
    _firm(session, firm_id)
    job = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.id == job_id, TallySyncJob.tenant_id == firm_id
        )
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not job.r2_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This job never uploaded a masters file.",
        )
    try:
        data = get_object(job.r2_key)
    except R2NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud storage is not configured.",
        ) from exc
    return Response(
        content=data,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="tally-masters-{job_id}.xml"'
        },
    )


@router.post("/sync-jobs/{job_id}/retry", response_model=TallySyncJobOut)
def retry_sync_job(
    firm_id: str, job_id: str, session: SessionDep
) -> TallySyncJob:
    _firm(session, firm_id)
    job = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.id == job_id, TallySyncJob.tenant_id == firm_id
        )
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status != "error":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a failed job can be retried.",
        )
    company = _load_company(session, firm_id)
    assert_no_pull_in_flight(session, firm_id)
    assert_tally_reachable(session, company)
    return enqueue_pull_masters(session, company)

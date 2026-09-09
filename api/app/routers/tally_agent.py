"""Tally companion agent API — shop checkin, backup upload, admin status.

  POST /api/tally-agent/checkin          — heartbeat + per-module status, returns queued outbox
  POST /api/tally-agent/upload-request   — pre-signed R2 PUT URL for a landed backup file
  POST /api/tally-agent/upload-confirm   — mark an upload confirmed/failed
  GET  /api/tally-agent/admin/shops      — Fleek-staff status view (PlatformAdmin)

Auth for the first three is `ShopAuth` (X-Shop-Key header, one key per shop,
issued by `tools.make_backup_shop`) — a distinct, machine-to-machine scheme
from the JWT bearer used everywhere else in this API. The admin listing
reuses the existing `PlatformAdmin` gate, same as `/api/admin/*`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.backup_storage import R2NotConfigured, presigned_put_url
from app.deps import PlatformAdmin, SessionDep, ShopAuth
from app.models import AgentOutboxItem, BackupShop, BackupUpload, TallyCompany, TallySyncJob
from app.schemas_tally import JobResultIn, JobStatusPingIn, TallySyncJobOut
from app.schemas_tally_agent import (
    OutboxItemOut,
    ShopCheckinIn,
    ShopCheckinOut,
    ShopStatusOut,
    UploadConfirmIn,
    UploadConfirmOut,
    UploadRequestIn,
    UploadRequestOut,
)
from app.services.tally.jobs import mark_job_sent, record_agent_status
from app.services.tally.pull import process_pull_result

router = APIRouter(prefix="/api/tally-agent", tags=["tally-agent"])


# --------------------------------------------------------------------------
# shop-authenticated (Windows tool)
# --------------------------------------------------------------------------


@router.post("/checkin", response_model=ShopCheckinOut)
def checkin(body: ShopCheckinIn, shop: ShopAuth, session: SessionDep) -> ShopCheckinOut:
    now = datetime.now(UTC)
    shop.last_checkin_at = now
    if body.error:
        shop.last_error = body.error[:1000]
        shop.last_error_at = now
    if body.tally_reachable is not None:
        shop.last_tally_status = body.tally_reason or (
            "connected" if body.tally_reachable else "unknown"
        )
        if body.tally_reachable:
            shop.last_tally_ok_at = now
    session.flush()

    outbox = list(
        session.scalars(
            select(AgentOutboxItem)
            .where(AgentOutboxItem.shop_id == shop.id, AgentOutboxItem.status == "queued")
            .order_by(AgentOutboxItem.created_at)
        ).all()
    )
    # `tally` items are dispatched once: flip them to `sent` here and advance
    # the paired sync job queued -> sent. Other modules' items keep their
    # existing (pre-F1a) behaviour of staying `queued` until that module
    # gains its own drain-confirm.
    for item in outbox:
        if item.module != "tally":
            continue
        item.status = "sent"
        job = session.scalar(
            select(TallySyncJob).where(TallySyncJob.outbox_item_id == item.id)
        )
        if job is not None:
            mark_job_sent(session, job)
    session.flush()

    return ShopCheckinOut(
        shop_id=shop.id,
        checked_in_at=now,
        outbox=[OutboxItemOut.model_validate(o) for o in outbox],
    )


@router.post("/jobs/{job_id}/result", response_model=TallySyncJobOut)
def job_result(
    job_id: str, body: JobResultIn, shop: ShopAuth, session: SessionDep
) -> TallySyncJob:
    """Agent -> backend: a `tally_sync_job` finished on the shop side.

    For `pull_masters`, `status='ok'` carries the R2 key of the uploaded
    masters XML; the backend downloads + parses + stages it. Never 500s —
    a bad payload becomes a job `error`.
    """
    job = _job_for_shop(session, job_id, shop.id)
    process_pull_result(
        session,
        job,
        ok=(body.status == "ok"),
        r2_key=body.r2_key,
        agent_error=body.error,
    )
    return job


def _job_for_shop(session: SessionDep, job_id: str, shop_id: str) -> TallySyncJob:
    job = session.scalar(select(TallySyncJob).where(TallySyncJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    company = session.get(TallyCompany, job.company_id)
    if company is None or company.shop_id != shop_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This job is not served by this shop.",
        )
    return job


@router.post("/jobs/{job_id}/status", response_model=TallySyncJobOut)
def job_status_ping(
    job_id: str, body: JobStatusPingIn, shop: ShopAuth, session: SessionDep
) -> TallySyncJob:
    """Agent -> backend: a 'not ready' reason (Tally closed / no company
    open) instead of a real result. Records it so the Pull step indicator
    can show "Waiting on Tally" and the lazy auto-cancel has something to
    key on. The job stays non-terminal — a later poll retries.
    """
    job = _job_for_shop(session, job_id, shop.id)
    record_agent_status(session, job, body.agent_status)
    return job


@router.post("/upload-request", response_model=UploadRequestOut)
def upload_request(
    body: UploadRequestIn, shop: ShopAuth, session: SessionDep
) -> UploadRequestOut:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    r2_key = f"{shop.id}/{ts}_{body.filename}"

    try:
        put_url, expires_in = presigned_put_url(r2_key)
    except R2NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud storage is not configured",
        ) from exc

    upload = BackupUpload(
        shop_id=shop.id,
        filename=body.filename,
        size_bytes=body.size_bytes,
        r2_key=r2_key,
        status="pending",
    )
    session.add(upload)
    session.flush()

    return UploadRequestOut(
        upload_id=upload.id, put_url=put_url, r2_key=r2_key, expires_in=expires_in
    )


@router.post("/upload-confirm", response_model=UploadConfirmOut)
def upload_confirm(
    body: UploadConfirmIn, shop: ShopAuth, session: SessionDep
) -> UploadConfirmOut:
    upload = session.scalar(
        select(BackupUpload).where(
            BackupUpload.id == body.upload_id, BackupUpload.shop_id == shop.id
        )
    )
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")

    upload.status = body.status
    if body.status == "confirmed":
        upload.uploaded_at = datetime.now(UTC)
    session.flush()
    return UploadConfirmOut(upload_id=upload.id, status=upload.status)


# --------------------------------------------------------------------------
# admin (Fleek staff)
# --------------------------------------------------------------------------


@router.get("/admin/shops", response_model=list[ShopStatusOut])
def list_shops(user: PlatformAdmin, session: SessionDep) -> list[ShopStatusOut]:
    shops = list(session.scalars(select(BackupShop).order_by(func.lower(BackupShop.name))).all())

    upload_stats: dict[str, tuple[datetime | None, int]] = {}
    rows = session.execute(
        select(
            BackupUpload.shop_id,
            func.max(BackupUpload.uploaded_at),
            func.count(),
        )
        .where(BackupUpload.status == "confirmed")
        .group_by(BackupUpload.shop_id)
    ).all()
    for shop_id, last_at, count in rows:
        upload_stats[shop_id] = (last_at, count)

    out: list[ShopStatusOut] = []
    for s in shops:
        last_upload_at, upload_count = upload_stats.get(s.id, (None, 0))
        out.append(
            ShopStatusOut(
                id=s.id,
                name=s.name,
                is_active=s.is_active,
                tenant_id=s.tenant_id,
                last_checkin_at=s.last_checkin_at,
                last_error=s.last_error,
                last_error_at=s.last_error_at,
                last_upload_at=last_upload_at,
                upload_count=upload_count,
            )
        )
    return out

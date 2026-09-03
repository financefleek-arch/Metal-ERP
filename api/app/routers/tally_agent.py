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
from app.models import AgentOutboxItem, BackupShop, BackupUpload
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
    session.flush()

    outbox = list(
        session.scalars(
            select(AgentOutboxItem)
            .where(AgentOutboxItem.shop_id == shop.id, AgentOutboxItem.status == "queued")
            .order_by(AgentOutboxItem.created_at)
        ).all()
    )
    return ShopCheckinOut(
        shop_id=shop.id,
        checked_in_at=now,
        outbox=[OutboxItemOut.model_validate(o) for o in outbox],
    )


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

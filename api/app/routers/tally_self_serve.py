"""Tenant-scoped Tally agent surface — the firm's own login, not the
platform-admin console.

Mirrors the operator's "Download installer" (`/api/admin/firms/{id}/
tally-shop/installer`) so a shop can self-serve the same cached zip without
Fleek forwarding a link. Also exposes the live health signal (`agent_online`,
`tally_status`) for a "Connect your Tally" card on the firm's dashboard.

`WriteUser` scopes everything to `user.tenant_id` — a firm can only ever see
or download its own agent's installer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.backup_storage import R2NotConfigured, get_object
from app.deps import SessionDep, WriteUser
from app.models import BackupShop, BackupUpload, Invoice, TallySyncJob
from app.schemas_admin import (
    FirmBackupFileOut,
    FirmBackupListOut,
    FirmBackupSetOut,
    FirmTallyShopOut,
)
from app.schemas_tally import TallyPushBlockersOut, TallySyncJobOut
from app.services.tally.agent_health import agent_online, assert_tally_reachable
from app.services.tally.backup_retention import effective_retention_count
from app.services.tally.installer import installer_filename
from app.services.tally.jobs import assert_no_push_in_flight, enqueue_push_sales
from app.services.tally.push_readiness import get_tally_company, push_blockers

router = APIRouter(prefix="/api/tally", tags=["tally-self-serve"])


def _owned_invoice(session: SessionDep, user: WriteUser, invoice_id: str) -> Invoice:
    inv = session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return inv


def _own_shop(session: SessionDep, user: WriteUser) -> BackupShop | None:
    return session.scalar(select(BackupShop).where(BackupShop.tenant_id == user.tenant_id))


@router.get("/agent-status", response_model=FirmTallyShopOut)
def agent_status(user: WriteUser, session: SessionDep) -> FirmTallyShopOut:
    shop = _own_shop(session, user)
    if shop is None:
        return FirmTallyShopOut(provisioned=False)
    return FirmTallyShopOut(
        provisioned=True,
        shop_id=shop.id,
        is_active=shop.is_active,
        last_checkin_at=shop.last_checkin_at,
        installer_ready=shop.installer_r2_key is not None,
        agent_online=agent_online(shop),
        tally_status=shop.last_tally_status,
        tally_ok_at=shop.last_tally_ok_at,
    )


@router.get("/installer")
def download_own_installer(user: WriteUser, session: SessionDep) -> Response:
    """Stream this firm's cached installer zip — same artifact the operator
    can download, self-served from the firm's own login."""
    shop = _own_shop(session, user)
    if shop is None or shop.installer_r2_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tally sync hasn't been set up for this firm yet — ask Fleek to enable it.",
        )
    try:
        blob = get_object(shop.installer_r2_key)
    except R2NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud storage is not configured.",
        ) from exc
    fname = installer_filename(user.tenant.legal_name)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# --------------------------------------------------------------------------
# cloud backups — list + download, tenant-scoped. No in-place restore: the
# firm downloads the file and runs Tally's own Restore (Alt+F3 -> Data ->
# Restore), which is an exclusive-access company operation the HTTP gateway
# can't perform.
# --------------------------------------------------------------------------


@router.get("/backups", response_model=FirmBackupListOut)
def list_own_backups(user: WriteUser, session: SessionDep) -> FirmBackupListOut:
    shop = _own_shop(session, user)
    if shop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tally sync hasn't been set up for this firm yet — ask Fleek to enable it.",
        )
    rows = list(
        session.scalars(
            select(BackupUpload).where(
                BackupUpload.shop_id == shop.id,
                BackupUpload.status == "confirmed",
            )
        )
    )

    # Group by set_id; a row without one is its own set, keyed by row id so
    # it can never merge with another.
    grouped: dict[tuple[bool, str], list[BackupUpload]] = {}
    for r in rows:
        gkey = (True, r.set_id) if r.set_id else (False, r.id)
        grouped.setdefault(gkey, []).append(r)

    epoch = datetime.min.replace(tzinfo=UTC)
    sets: list[FirmBackupSetOut] = []
    for (has_set_id, identifier), members in grouped.items():
        members.sort(key=lambda m: m.uploaded_at or epoch, reverse=True)
        newest = next((m.uploaded_at for m in members if m.uploaded_at), None)
        sets.append(
            FirmBackupSetOut(
                set_id=identifier if has_set_id else None,
                uploaded_at=newest,
                total_bytes=sum(m.size_bytes for m in members),
                files=[FirmBackupFileOut.model_validate(m) for m in members],
            )
        )
    sets.sort(key=lambda s: s.uploaded_at or epoch, reverse=True)

    return FirmBackupListOut(
        retention_count=effective_retention_count(shop),
        sets=sets,
    )


@router.get("/backups/{upload_id}/download")
def download_own_backup(
    upload_id: str, user: WriteUser, session: SessionDep
) -> Response:
    """Stream one confirmed backup file. The firm feeds this to Tally's own
    Restore — there is no server-triggered restore."""
    shop = _own_shop(session, user)
    upload = session.scalar(
        select(BackupUpload).where(BackupUpload.id == upload_id)
    )
    if (
        shop is None
        or upload is None
        or upload.shop_id != shop.id
        or upload.status != "confirmed"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup not found")
    try:
        blob = get_object(upload.r2_key)
    except R2NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud storage is not configured.",
        ) from exc
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{upload.filename}"'},
    )


# --------------------------------------------------------------------------
# push a sales voucher (F1b-1) — manual re-push, tenant-scoped
# --------------------------------------------------------------------------


@router.get("/invoices/{invoice_id}/push-status", response_model=TallyPushBlockersOut)
def get_own_push_status(
    invoice_id: str, user: WriteUser, session: SessionDep
) -> TallyPushBlockersOut:
    invoice = _owned_invoice(session, user, invoice_id)
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
def push_own_invoice(
    invoice_id: str, user: WriteUser, session: SessionDep
) -> TallySyncJob:
    invoice = _owned_invoice(session, user, invoice_id)
    blockers = push_blockers(session, invoice)
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(b.message for b in blockers),
        )
    company = get_tally_company(session, user.tenant_id)
    assert company is not None  # push_blockers() already confirmed this
    assert_no_push_in_flight(session, user.tenant_id, invoice_id)
    assert_tally_reachable(session, company)
    return enqueue_push_sales(session, company, invoice)

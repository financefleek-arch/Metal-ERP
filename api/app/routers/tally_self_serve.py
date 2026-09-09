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

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.backup_storage import R2NotConfigured, get_object
from app.deps import SessionDep, WriteUser
from app.models import BackupShop
from app.schemas_admin import FirmTallyShopOut
from app.services.tally.agent_health import agent_online
from app.services.tally.installer import installer_filename

router = APIRouter(prefix="/api/tally", tags=["tally-self-serve"])


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

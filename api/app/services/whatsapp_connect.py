"""The shop owner's WhatsApp page, mirrored from Fleek's advisor page (docs/PARITY-whatsapp-connect.md).

Fleek's advisor page calls /api/advisor/whatsapp-integrations (status, connect, retry-registration,
disconnect). A shop owner has no Fleek session, so this app's server makes the same calls to Fleek,
signed, for the shop's organisation (/api/internal/metal-erp/orgs/<id>/whatsapp-integrations/...).
Fleek runs the very same functions and answers with the very same bodies; nothing is decided here.

This app keeps ONE thing: a cache of the number (`tenant_whatsapp_config`), because it sends messages
and matches status webhooks from its own server and cannot read Fleek's database. `sync_cache`
rewrites it from the connection Fleek reports, after every call.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TenantWhatsappConfig
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60.0  # connect copies templates on Fleek's side


class FleekUnavailable(Exception):
    """Fleek could not be reached, or this app is not set up to reach it."""


def call(
    method: str,
    fleek_org_id: str,
    suffix: str = "",
    payload: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
) -> tuple[int, dict[str, Any]]:
    """-> (http status, body) exactly as Fleek answered, including its 4xx/5xx bodies.

    Raises FleekUnavailable only when Fleek cannot be reached."""
    s = get_settings()
    if not s.control_plane_url or not s.control_plane_secret:
        raise FleekUnavailable("WhatsApp connection is not available yet")
    path = f"/api/internal/metal-erp/orgs/{fleek_org_id}/whatsapp-integrations{suffix}"
    body = json.dumps(payload).encode() if payload is not None else b""
    stamp = str(int(datetime.now(UTC).timestamp()))
    headers = {
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: sign(s.control_plane_secret, timestamp=stamp, method=method, path=path, body=body),
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        resp = http.request(method, s.control_plane_url.rstrip("/") + path, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise FleekUnavailable(f"Could not reach Fleek: {exc.__class__.__name__}") from exc
    finally:
        if own:
            http.close()
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {}


def sync_cache(session: Session, tenant_id: str, fleek_status: dict[str, Any]) -> None:
    """Rewrite this firm's number cache from Fleek's status body (it carries `connection` for this server).

    Sends from the number only when Fleek says it is connected AND registered: the same condition
    under which Fleek's own sends can succeed."""
    view = fleek_status.get("connection")
    cfg = session.scalar(select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == tenant_id))
    if not view:
        if cfg is not None:
            cfg.is_active = False
        return
    active = not fleek_status.get("needs_registration")
    if active:
        clash = session.scalar(
            select(TenantWhatsappConfig).where(
                TenantWhatsappConfig.phone_number_id == view["phone_number_id"],
                TenantWhatsappConfig.tenant_id != tenant_id,
                TenantWhatsappConfig.is_active.is_(True),
            )
        )
        if clash is not None:  # the webhook finds the firm by number: one active firm per number
            log.warning("whatsapp number %s is already active for another firm", view["phone_number_id"])
            active = False
    if cfg is None:
        cfg = TenantWhatsappConfig(
            tenant_id=tenant_id, phone_number_id=view["phone_number_id"], waba_id=view["waba_id"]
        )
        session.add(cfg)
    cfg.phone_number_id = view["phone_number_id"]
    cfg.waba_id = view["waba_id"]
    cfg.display_phone_number = view.get("display_phone_number")
    cfg.is_active = active
    session.flush()

"""WhatsApp Business API — send an invoice, and the Meta status webhook.

  GET  /api/whatsapp/webhook        — Meta subscription handshake
  POST /api/whatsapp/webhook        — delivery/read/failed status callbacks
  POST /api/invoices/{id}/whatsapp  — send this invoice to its party

The webhook is unauthenticated (Meta sends no bearer token); it is protected
by an `X-Hub-Signature-256` HMAC over the raw body, keyed with the process
`whatsapp_app_secret`. Fail-closed in production — a missing secret means the
route refuses rather than skips verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import SessionDep, WriteUser
from app.models import Invoice
from app.services.whatsapp import (
    TEMPLATE_BODY_PARAMS,
    WhatsappError,
    WhatsappNotConfigured,
    handle_status_webhook,
    send_invoice,
    verify_webhook_challenge,
)

log = logging.getLogger("whatsapp")
_settings = get_settings()

router = APIRouter(tags=["whatsapp"])


# --------------------------------------------------------------------------
# webhook
# --------------------------------------------------------------------------


@router.get("/api/whatsapp/webhook", include_in_schema=False)
def webhook_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
) -> Response:
    challenge = verify_webhook_challenge(
        {
            "hub.mode": hub_mode,
            "hub.challenge": hub_challenge,
            "hub.verify_token": hub_verify_token,
        }
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verification failed")
    return Response(content=challenge, media_type="text/plain")


@router.post("/api/whatsapp/webhook", include_in_schema=False)
async def webhook_receive(request: Request, session: SessionDep) -> dict[str, str]:
    raw = await request.body()

    secret = _settings.whatsapp_app_secret or ""
    if not secret:
        if _settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="webhook verification is not configured",
            )
        log.warning("whatsapp webhook: no app secret set — skipping signature check (dev only)")
    else:
        header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(header, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature"
            )

    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed JSON"
        ) from None

    try:
        handle_status_webhook(session, payload)
    except Exception:  # never 500 back at Meta — it will retry-storm
        log.exception("whatsapp webhook: error handling payload")
    return {"status": "ok"}


# --------------------------------------------------------------------------
# send an invoice
# --------------------------------------------------------------------------


class InvoiceWhatsappSend(BaseModel):
    template_name: str = Field(description="approved WABA template; one of TEMPLATE_BODY_PARAMS")


class InvoiceWhatsappOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    template_name: str
    to_phone: str
    wa_message_id: str | None = None
    error: str | None = None


# Mounted on the invoices path but defined here to keep all WhatsApp wiring
# in one module.
invoice_router = APIRouter(prefix="/api/invoices", tags=["whatsapp"])


@invoice_router.post("/{invoice_id}/whatsapp", response_model=InvoiceWhatsappOut)
def send_invoice_whatsapp(
    invoice_id: str,
    body: InvoiceWhatsappSend,
    user: WriteUser,
    session: SessionDep,
) -> InvoiceWhatsappOut:
    if body.template_name not in TEMPLATE_BODY_PARAMS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"template_name must be one of {sorted(TEMPLATE_BODY_PARAMS)}",
        )

    inv = session.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.lines))
    )
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    try:
        msg = send_invoice(session, inv, template_name=body.template_name)
    except WhatsappNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except WhatsappError as exc:
        # message row was left in `failed`; report why
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return InvoiceWhatsappOut.model_validate(msg)

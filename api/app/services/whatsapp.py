"""WhatsApp Business send + status-webhook handling (Meta Cloud API).

Ported in spirit from fleek-backend's `services/whatsapp_service.py`, but:

  * FastAPI + SQLAlchemy ORM, not Flask + raw SQL.
  * We reuse the "FleekWA" Meta app exactly as fleek does: one process-wide
    System User token (`whatsapp_api_key`) covers every number. Per-firm rows
    in `tenant_whatsapp_config` only carry the `phone_number_id` that selects
    which number a firm sends from — no per-firm token, nothing secret at rest.
  * Business-initiated messages go out as an approved **template**
    (`type: "template"`), not free text — free text only works inside the 24h
    customer-service window, which a "your invoice is ready" message never is.
  * The PDF is sent as a **document attachment**: upload to `/{pnid}/media`,
    then reference the returned media id in the template's header component.

Template names here must exactly match templates registered & approved in
each firm's WABA. Placeholders map positionally to Meta's `{{1}}`, `{{2}}` …
body parameters, in the order listed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Invoice, Party, TenantWhatsappConfig, WhatsappMessage

log = logging.getLogger("whatsapp")
_settings = get_settings()

_TIMEOUT = 15  # seconds — Meta's Graph API is usually sub-second


# Body-parameter order per template. The header (document) component is added
# separately by the send call when there is a PDF to attach.
TEMPLATE_BODY_PARAMS: dict[str, tuple[str, ...]] = {
    # "Hi {{1}}, your invoice {{2}} for {{3}} is ready."
    "invoice_ready": ("party_name", "invoice_number", "grand_total"),
    # "Hi {{1}}, invoice {{2}} for {{3}} is due. Please arrange payment."
    "payment_reminder": ("party_name", "invoice_number", "grand_total"),
}


class WhatsappError(Exception):
    """Send failed. The caller should surface this as a 4xx/5xx and leave the
    `whatsapp_message` row in `failed` — never half-recorded as sent."""


class WhatsappNotConfigured(WhatsappError):
    """Process-wide config (`whatsapp_api_key` / `whatsapp_app_secret`) or the
    firm's `tenant_whatsapp_config` row is missing or inactive."""


# --------------------------------------------------------------------------
# config lookup
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedConfig:
    tenant_id: str
    phone_number_id: str
    access_token: str  # the process-wide System User token; per-firm number,
    # shared credential — see module docstring.


def get_config(session: Session, tenant_id: str) -> ResolvedConfig:
    token = _settings.whatsapp_api_key
    if not token or not _settings.whatsapp_app_secret:
        raise WhatsappNotConfigured(
            "whatsapp_api_key / whatsapp_app_secret are not set"
        )
    row = session.scalar(
        select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == tenant_id)
    )
    if row is None or not row.is_active:
        raise WhatsappNotConfigured("this firm has no active WhatsApp configuration")
    return ResolvedConfig(
        tenant_id=tenant_id,
        phone_number_id=row.phone_number_id,
        access_token=token,
    )


def config_for_phone_number_id(
    session: Session, phone_number_id: str
) -> TenantWhatsappConfig | None:
    """Webhook path: Meta sends no auth, only the number's id in
    `changes[].value.metadata.phone_number_id`."""
    return session.scalar(
        select(TenantWhatsappConfig).where(
            TenantWhatsappConfig.phone_number_id == phone_number_id
        )
    )


# --------------------------------------------------------------------------
# Meta Cloud API calls
# --------------------------------------------------------------------------


def _api_base(cfg: ResolvedConfig) -> str:
    return f"https://graph.facebook.com/{_settings.whatsapp_api_version}/{cfg.phone_number_id}"


def _headers(cfg: ResolvedConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.access_token}"}


def upload_media(cfg: ResolvedConfig, pdf_path: str, *, filename: str) -> str:
    """POST /{pnid}/media — returns the media id to reference in a message.
    Meta holds uploaded media ~30 days; we upload fresh per send."""
    path = Path(pdf_path)
    if not path.exists():
        raise WhatsappError(f"PDF not found on disk: {pdf_path}")
    with path.open("rb") as fh:
        resp = httpx.post(
            f"{_api_base(cfg)}/media",
            headers=_headers(cfg),
            data={"messaging_product": "whatsapp", "type": "application/pdf"},
            files={"file": (filename, fh, "application/pdf")},
            timeout=_TIMEOUT,
        )
    if resp.status_code >= 400:
        raise WhatsappError(f"media upload failed: {resp.status_code} {resp.text[:300]}")
    media_id = resp.json().get("id")
    if not media_id:
        raise WhatsappError(f"media upload returned no id: {resp.text[:300]}")
    return media_id


def _send_template_message(
    cfg: ResolvedConfig,
    *,
    to_phone: str,
    template_name: str,
    body_params: list[str],
    document_media_id: str | None,
    document_filename: str | None,
    lang_code: str = "en",
) -> str:
    components: list[dict] = []
    if document_media_id:
        components.append(
            {
                "type": "header",
                "parameters": [
                    {
                        "type": "document",
                        "document": {
                            "id": document_media_id,
                            "filename": document_filename or "invoice.pdf",
                        },
                    }
                ],
            }
        )
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code},
            "components": components,
        },
    }
    resp = httpx.post(
        f"{_api_base(cfg)}/messages",
        headers={**_headers(cfg), "Content-Type": "application/json"},
        json=payload,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise WhatsappError(f"send failed: {resp.status_code} {resp.text[:400]}")
    try:
        return resp.json()["messages"][0]["id"]
    except (KeyError, IndexError, ValueError) as exc:
        raise WhatsappError(f"send response had no message id: {resp.text[:300]}") from exc


# --------------------------------------------------------------------------
# high-level: send an invoice
# --------------------------------------------------------------------------


def _phone_e164(raw: str) -> str:
    """Meta wants digits only, country code included, no '+'. Assumes India
    (91) for a bare 10-digit number — matches how parties are stored today."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def _pdf_filename(invoice: Invoice, party: Party) -> str:
    num = invoice.number or "draft"
    return f"Invoice-{num}.pdf"


def send_invoice(
    session: Session,
    invoice: Invoice,
    *,
    template_name: str,
) -> WhatsappMessage:
    """Send `invoice` to its party over WhatsApp as `template_name`.

    Guards (raise WhatsappError, nothing sent):
      * template unknown
      * invoice not finalized / has no party / party not opted in / no phone
      * firm has no active WhatsApp config

    On success the returned `WhatsappMessage` is `sent` with `wa_message_id`;
    on a Meta rejection it is `failed` with `error`, and the exception is
    re-raised so the route returns non-2xx.
    """
    if template_name not in TEMPLATE_BODY_PARAMS:
        raise WhatsappError(f"unknown template: {template_name!r}")

    from app.models._mixins import InvoiceStatus

    if invoice.status != InvoiceStatus.final:
        raise WhatsappError("invoice is not finalized")
    party = invoice.party
    if party is None:
        raise WhatsappError("invoice has no party")
    if not party.whatsapp_optin:
        raise WhatsappError("party has not opted in to WhatsApp messages")
    if not party.phone:
        raise WhatsappError("party has no phone number")

    cfg = get_config(session, invoice.tenant_id)

    grand_total = invoice.grand_total
    # {{3}} is declared as a Number variable in the WhatsApp template, so it
    # must be a bare numeric string — no ₹, no thousands separators. The ₹
    # symbol lives in the template's static text ("Amount: ₹{{3}}").
    total_str = f"{grand_total:.2f}" if grand_total is not None else "0.00"
    param_values = {
        "party_name": party.legal_name,
        "invoice_number": str(invoice.number or ""),
        "grand_total": total_str,
    }
    body_params = [param_values[k] for k in TEMPLATE_BODY_PARAMS[template_name]]

    to_phone = _phone_e164(party.phone)
    msg = WhatsappMessage(
        tenant_id=invoice.tenant_id,
        party_id=party.id,
        invoice_id=invoice.id,
        template_name=template_name,
        to_phone=to_phone,
        status="pending",
    )
    session.add(msg)
    session.flush()

    media_id: str | None = None
    filename = _pdf_filename(invoice, party)
    try:
        if invoice.pdf_path and Path(invoice.pdf_path).exists():
            media_id = upload_media(cfg, invoice.pdf_path, filename=filename)
            msg.media_id = media_id
        wa_id = _send_template_message(
            cfg,
            to_phone=to_phone,
            template_name=template_name,
            body_params=body_params,
            document_media_id=media_id,
            document_filename=filename,
        )
    except WhatsappError as exc:
        msg.status = "failed"
        msg.error = str(exc)[:1000]
        session.flush()
        raise

    msg.status = "sent"
    msg.wa_message_id = wa_id
    msg.sent_at = datetime.now(UTC)
    session.flush()
    return msg


# --------------------------------------------------------------------------
# webhook
# --------------------------------------------------------------------------


def verify_webhook_challenge(params: dict) -> str | None:
    """GET handshake. Meta echoes `hub.challenge` back iff `hub.verify_token`
    matches ours. We reuse `whatsapp_app_secret` as that verify token (one
    fewer secret). Returns None on any mismatch — the route then 403s."""
    secret = _settings.whatsapp_app_secret or ""
    if (
        params.get("hub.mode") == "subscribe"
        and secret
        and params.get("hub.verify_token") == secret
    ):
        return params.get("hub.challenge", "")
    return None


def handle_status_webhook(session: Session, payload: dict) -> None:
    """POST body. Walk entry[].changes[].value.statuses[] and move the
    matching `whatsapp_message` row along delivered/read/failed. Rows are
    matched by `wa_message_id`; a status for an unknown id is ignored."""
    now = datetime.now(UTC)
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for st in value.get("statuses", []):
                wa_id = st.get("id")
                status = st.get("status")
                if not wa_id or not status:
                    continue
                msg = session.scalar(
                    select(WhatsappMessage).where(
                        WhatsappMessage.wa_message_id == wa_id
                    )
                )
                if msg is None:
                    continue
                if status == "delivered" and msg.status in ("pending", "sent"):
                    msg.status = "delivered"
                    msg.delivered_at = now
                elif status == "read":
                    msg.status = "read"
                    msg.read_at = now
                    if msg.delivered_at is None:
                        msg.delivered_at = now
                elif status == "failed":
                    msg.status = "failed"
                    errors = st.get("errors") or []
                    if errors:
                        msg.error = str(errors[0])[:1000]
    session.flush()

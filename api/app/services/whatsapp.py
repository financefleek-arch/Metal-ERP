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
from datetime import UTC, date, datetime
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
# {{1}} = customer (party) name, {{2}} = invoice number, {{3}} = amount.
# Approved invoice_ready body:
#   "Hi {{1}}, your invoice {{2}} is ready. Amount: ₹{{3}}. The PDF is attached."
# No firm/tenant name is ever put into a message — only the three values below.
TEMPLATE_BODY_PARAMS: dict[str, tuple[str, ...]] = {
    "invoice_ready": ("party_name", "invoice_number", "grand_total"),
    "payment_reminder": ("party_name", "invoice_number", "grand_total"),
    # F3b — party account statement. Body:
    #   "Hello {{1}}, here is your account statement. Total due: ₹{{2}}.
    #    Please reply PAID once settled." + PDF document header.
    "account_statement": ("party_name", "total_due"),
}


class WhatsappError(Exception):
    """Send failed. The caller should surface this as a 4xx/5xx and leave the
    `whatsapp_message` row in `failed` — never half-recorded as sent."""


class WhatsappNotConfigured(WhatsappError):
    """`whatsapp_api_key` is unset, or the firm's `tenant_whatsapp_config`
    row is missing or inactive."""


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
    # Only the System User token is needed to *send*. `whatsapp_app_secret`
    # is used solely to verify inbound status webhooks (see the webhook
    # route + verify_webhook_challenge) — its absence degrades receipts, it
    # doesn't block sending.
    token = _settings.whatsapp_api_key
    if not token:
        raise WhatsappNotConfigured("whatsapp_api_key is not set")
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
# test send — prove a firm's config works without a real invoice
# --------------------------------------------------------------------------


# A valid, minimal one-page PDF ("Fleek — WhatsApp test document"). Hand-built
# so the test path has no WeasyPrint / native-lib dependency (see
# services/invoices/pdf.py's note about bare dev boxes).
_TEST_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 120] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>endobj\n"
    b"4 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
    b"5 0 obj<< /Length 74 >>stream\n"
    b"BT /F1 14 Tf 20 60 Td (Fleek - WhatsApp test document) Tj ET\n"
    b"endstream endobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000241 00000 n \n"
    b"0000000312 00000 n \n"
    b"trailer<< /Size 6 /Root 1 0 R >>\n"
    b"startxref\n437\n%%EOF\n"
)


def send_test_message(
    session: Session,
    tenant_id: str,
    *,
    to_phone: str,
    template_name: str = "invoice_ready",
    with_document: bool = True,
) -> WhatsappMessage:
    """Send `template_name` to `to_phone` using the firm's configured number,
    with dummy body params (and a throwaway PDF header when `with_document`).

    No invoice, party, or opt-in required — this only proves the firm's
    `tenant_whatsapp_config` + the process token + the approved template all
    line up. Records a `whatsapp_message` row (party_id / invoice_id NULL)
    exactly like a real send, so the webhook can still move it to
    delivered/read.
    """
    if template_name not in TEMPLATE_BODY_PARAMS:
        raise WhatsappError(f"unknown template: {template_name!r}")

    cfg = get_config(session, tenant_id)
    to = _phone_e164(to_phone)
    if len(to) < 10:
        raise WhatsappError(f"recipient phone looks invalid: {to_phone!r}")

    # Dummy values, positional per the template's declared param order.
    dummy = {
        "party_name": "Test Customer",
        "invoice_number": "TEST-0001",
        "grand_total": "1234.00",
    }
    body_params = [dummy[k] for k in TEMPLATE_BODY_PARAMS[template_name]]

    msg = WhatsappMessage(
        tenant_id=tenant_id,
        party_id=None,
        invoice_id=None,
        template_name=template_name,
        to_phone=to,
        status="pending",
    )
    session.add(msg)
    session.flush()

    media_id: str | None = None
    try:
        if with_document:
            media_id = _upload_media_bytes(
                cfg, _TEST_PDF_BYTES, filename="Fleek-test.pdf"
            )
            msg.media_id = media_id
        wa_id = _send_template_message(
            cfg,
            to_phone=to,
            template_name=template_name,
            body_params=body_params,
            document_media_id=media_id,
            document_filename="Fleek-test.pdf",
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


def _upload_media_bytes(cfg: ResolvedConfig, data: bytes, *, filename: str) -> str:
    """Like `upload_media` but from an in-memory buffer (test PDF)."""
    resp = httpx.post(
        f"{_api_base(cfg)}/media",
        headers=_headers(cfg),
        data={"messaging_product": "whatsapp", "type": "application/pdf"},
        files={"file": (filename, data, "application/pdf")},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise WhatsappError(f"media upload failed: {resp.status_code} {resp.text[:300]}")
    media_id = resp.json().get("id")
    if not media_id:
        raise WhatsappError(f"media upload returned no id: {resp.text[:300]}")
    return media_id


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


def send_invoice(
    session: Session,
    invoice: Invoice,
    *,
    template_name: str,
    to_phone: str | None = None,
) -> WhatsappMessage:
    """Send `invoice` (as `template_name`, with its PDF attached) over WhatsApp.

    `to_phone` given  → send to that number, an explicit operator choice; the
                        party's stored phone is not consulted. `party_name` in
                        the message still comes from the invoice's party (or
                        "Customer" if the invoice has none).
    `to_phone` omitted → send to the invoice's party: requires the party to
                        exist and have a phone on file. (There is no separate
                        opt-in flag — an invoice the shop chose to send is the
                        consent.)

    Guards (raise WhatsappError, nothing sent): unknown template, invoice not
    finalized, no usable recipient, firm has no active WhatsApp config.

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
    if to_phone:
        recipient = _phone_e164(to_phone)
        if len(recipient) < 10:
            raise WhatsappError(f"recipient phone looks invalid: {to_phone!r}")
    else:
        if party is None:
            raise WhatsappError("invoice has no party")
        if not party.phone:
            raise WhatsappError("party has no phone number")
        recipient = _phone_e164(party.phone)

    cfg = get_config(session, invoice.tenant_id)

    grand_total = invoice.grand_total
    # {{3}} is declared as a Number variable in the WhatsApp template, so it
    # must be a bare numeric string — no ₹, no thousands separators. The ₹
    # symbol lives in the template's static text ("Amount: ₹{{3}}").
    total_str = f"{grand_total:.2f}" if grand_total is not None else "0.00"
    param_values = {
        "party_name": party.legal_name if party else "Customer",
        "invoice_number": str(invoice.number or ""),
        "grand_total": total_str,
    }
    body_params = [param_values[k] for k in TEMPLATE_BODY_PARAMS[template_name]]

    msg = WhatsappMessage(
        tenant_id=invoice.tenant_id,
        party_id=party.id if party else None,
        invoice_id=invoice.id,
        template_name=template_name,
        to_phone=recipient,
        status="pending",
    )
    session.add(msg)
    session.flush()

    from app.services.invoices.common import download_name

    media_id: str | None = None
    filename = download_name(invoice)
    try:
        if invoice.pdf_path and Path(invoice.pdf_path).exists():
            media_id = upload_media(cfg, invoice.pdf_path, filename=filename)
            msg.media_id = media_id
        wa_id = _send_template_message(
            cfg,
            to_phone=recipient,
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
# high-level: send a party account statement (F3b)
# --------------------------------------------------------------------------


def send_party_statement(
    session: Session,
    party_id: str,
    *,
    period: str,
    dt_from: date | None = None,
    dt_to: date | None = None,
    to_phone: str | None = None,
) -> WhatsappMessage:
    """Render the party's account statement for `period` and send it over
    WhatsApp (template `account_statement`, PDF as the document header).

    `to_phone` given  → send there (explicit operator choice); the party's
                        stored phone is not consulted.
    `to_phone` omitted → send to the party's stored phone; it must exist.

    Raises WhatsappError (nothing sent) on: empty window, party has no phone
    and no `to_phone`, firm has no active WhatsApp config. On a Meta
    rejection the `whatsapp_message` row is left `failed` and the exception
    re-raised. The row is `party_id` set / `invoice_id` NULL.
    """
    from app.services.statements import (
        StatementError,
        render_party_statement_pdf,
        resolve_period,
        statement_download_name,
    )

    party = session.get(Party, party_id)
    if party is None:
        raise WhatsappError("party not found")

    try:
        start, end = resolve_period(period, dt_from=dt_from, dt_to=dt_to)  # type: ignore[arg-type]
    except StatementError as exc:
        raise WhatsappError(str(exc)) from exc

    if to_phone:
        recipient = _phone_e164(to_phone)
        if len(recipient) < 10:
            raise WhatsappError(f"recipient phone looks invalid: {to_phone!r}")
    else:
        if not party.phone:
            raise WhatsappError("party has no phone number")
        recipient = _phone_e164(party.phone)

    cfg = get_config(session, party.tenant_id)

    try:
        pdf_path, data = render_party_statement_pdf(
            session, party_id, period_from=start, period_to=end
        )
    except StatementError as exc:
        raise WhatsappError(str(exc)) from exc
    if data.is_empty:
        raise WhatsappError("nothing happened this period — no statement to send")

    filename = statement_download_name(data)
    # {{2}} is a Number variable in the template — bare numeric string, no ₹.
    total_str = f"{data.total_due:.2f}"
    param_values = {"party_name": party.legal_name, "total_due": total_str}
    body_params = [param_values[k] for k in TEMPLATE_BODY_PARAMS["account_statement"]]

    msg = WhatsappMessage(
        tenant_id=party.tenant_id,
        party_id=party.id,
        invoice_id=None,
        template_name="account_statement",
        to_phone=recipient,
        status="pending",
    )
    session.add(msg)
    session.flush()

    media_id: str | None = None
    try:
        if Path(pdf_path).exists():
            media_id = upload_media(cfg, str(pdf_path), filename=filename)
            msg.media_id = media_id
        wa_id = _send_template_message(
            cfg,
            to_phone=recipient,
            template_name="account_statement",
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


def _fmt_wa_error(err: dict) -> str:
    """Meta's status error dict -> a short operator-readable line."""
    code = err.get("code")
    title = err.get("title") or err.get("message") or "delivery failed"
    detail = (err.get("error_data") or {}).get("details")
    parts = [str(title)]
    if code is not None:
        parts.append(f"(code {code})")
    if detail and detail != title:
        parts.append(f"- {detail}")
    return " ".join(parts)[:1000]


def handle_status_webhook(session: Session, payload: dict) -> None:
    """POST body. Walk entry[].changes[].value.statuses[] and move the
    matching `whatsapp_message` row along delivered/read/failed. Rows are
    matched by `wa_message_id`; a status for an unknown id is ignored — most
    callbacks on the shared FleekWA app belong to other apps (fan-in from
    fleek-backend)."""
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
                        msg.error = _fmt_wa_error(errors[0])
    session.flush()

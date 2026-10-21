"""Ask Fleek to send one WhatsApp message for a linked firm.

A firm linked to a Fleek organisation (`tenant.fleek_org_id`) does not call Meta itself. It hands the
message to Fleek, signed with the control-plane secret like every other call between the two, and Fleek
sends it from the firm's own number. Fleek knows which template is approved on the firm's account,
builds the message and answers with Meta's message id or a plain reason. This module makes that one
call; `services/whatsapp.py` decides when.

POST /api/internal/metal-erp/orgs/<fleek_org_id>/messages
  {"template_key", "to", "values": {name: text}, "links": {"order": token}?,
   "document": {"filename", "content_base64"}?, "request_id"?}
  -> 200 {"status": "sent", "provider_message_id"} | 4xx / 502 {"error", "message", ...}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import get_settings
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

# the same "Fleek cannot be reached" as the WhatsApp connect page
from app.services.whatsapp_connect import FleekUnavailable

# Fleek may upload the PDF to Meta and then send: each of those calls has its own limit on Fleek's side
TIMEOUT_SECONDS = 50.0

__all__ = ["FleekUnavailable", "request", "send"]


def request(
    method: str,
    fleek_org_id: str,
    suffix: str,
    payload: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
) -> tuple[int, dict[str, Any]]:
    """One signed call to /api/internal/metal-erp/orgs/<org>/<suffix>.

    -> (http status, body) exactly as Fleek answered, including its 4xx/5xx bodies.
    Raises FleekUnavailable only when Fleek cannot be reached."""
    s = get_settings()
    if not s.control_plane_url or not s.control_plane_secret:
        raise FleekUnavailable("Fleek is not configured for messages")
    path = f"/api/internal/metal-erp/orgs/{fleek_org_id}/{suffix}"
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
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


def send(
    fleek_org_id: str, payload: dict[str, Any], *, client: httpx.Client | None = None
) -> tuple[int, dict[str, Any]]:
    """Ask Fleek to send one message (POST .../messages). Same answer shape as `request`."""
    return request("POST", fleek_org_id, "messages", payload, client=client)

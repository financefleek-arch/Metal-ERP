"""One signed request to the Fleek control plane (fleek-backend /api/internal/metal-erp/*).

The same HMAC scheme every call between the two uses (services/control_plane_auth.py). Returns
Fleek's status and body unchanged; raises FleekUnavailable only when Fleek cannot be reached or this
server is not set up to reach it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import get_settings
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

TIMEOUT_SECONDS = 20.0


class FleekUnavailable(Exception):
    """Fleek could not be reached, or this app is not set up to reach it."""


def request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
) -> tuple[int, dict[str, Any]]:
    s = get_settings()
    if not s.control_plane_url or not s.control_plane_secret:
        raise FleekUnavailable("Fleek is not configured")
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

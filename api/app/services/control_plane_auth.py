"""Authentication of calls from the Fleek control plane (fleek-backend) to /api/internal/control-plane/*.

A shared secret signs each request, so there is no token to store or rotate per firm and no trust in the
network path. The signature covers the timestamp, method, path and body hash:

    X-Fleek-Timestamp: <unix seconds>
    X-Fleek-Signature: sha256=<hex hmac-sha256(secret, "<ts>\n<METHOD>\n<path>\n<sha256(body)>")>

A request outside the time window is refused (replay), and an unset secret refuses every call: the routes
are never open. Same posture as the Meta webhook check (raw bytes, constant-time compare, fail closed).
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from app.config import get_settings

MAX_SKEW_SECONDS = 300
TIMESTAMP_HEADER = "X-Fleek-Timestamp"
SIGNATURE_HEADER = "X-Fleek-Signature"


def sign(secret: str, *, timestamp: str, method: str, path: str, body: bytes) -> str:
    """The header value for a request. Used by the caller (and by tests)."""
    digest = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method.upper()}\n{path}\n{digest}".encode()
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def check_signature(
    secret: str | None,
    *,
    timestamp: str | None,
    signature: str | None,
    method: str,
    path: str,
    body: bytes,
    now: float | None = None,
) -> None:
    """Raises HTTPException 503 (no secret), 401 (missing, stale or wrong signature)."""
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Control plane access is not configured")
    if not timestamp or not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing signature")
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad timestamp") from None
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_SECONDS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Stale request")
    expected = sign(secret, timestamp=timestamp, method=method, path=path, body=body)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")


async def require_control_plane(request: Request) -> None:
    """FastAPI dependency for the control-plane router."""
    check_signature(
        get_settings().control_plane_secret,
        timestamp=request.headers.get(TIMESTAMP_HEADER),
        signature=request.headers.get(SIGNATURE_HEADER),
        method=request.method,
        path=request.url.path,
        body=await request.body(),
    )

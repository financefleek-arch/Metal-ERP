"""Cloudflare Turnstile check for the public sign-up doors (sign-up, send code, forgot password).

Each code is a paid WhatsApp message, so a bot that can call "send code" freely costs real money.
The token comes from the widget in the page; this checks it with Cloudflare before anything is sent.
Production without a secret refuses (fail closed); elsewhere an unset secret skips the check so
development and the test suite need no Cloudflare account.
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from app.config import get_settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MESSAGE = "Please complete the check and try again."


def verify(token: str | None, ip: str | None, *, client: httpx.Client | None = None) -> None:
    """Raises HTTPException: 400 bad or missing token; 503 unconfigured in production or Cloudflare down."""
    s = get_settings()
    if not s.turnstile_secret_key:
        if s.app_env == "production":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-up is not available right now")
        return
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MESSAGE)
    data = {"secret": s.turnstile_secret_key, "response": token}
    if ip:
        data["remoteip"] = ip
    own = client is None
    http = client or httpx.Client(timeout=8.0)
    try:
        resp = http.post(VERIFY_URL, data=data)
        ok = bool(resp.json().get("success"))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The check could not be completed. Try again."
        ) from None
    finally:
        if own:
            http.close()
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MESSAGE)

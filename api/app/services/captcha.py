"""CAPTCHA on the public sign-up doors (sign-up, send code, forgot password), through Fleek.

Each code is a paid WhatsApp message, so a bot that can call "send code" freely costs real money. The
page shows a Cloudflare Turnstile widget; this sends its token to Fleek, which holds the Cloudflare keys
in platform secrets and checks it. Metal ERP holds no Cloudflare credential and the secret key never
leaves Fleek.

Production fails closed: no key set in Fleek, or Fleek unreachable, refuses (503). Outside production an
unconfigured or unreachable check is skipped, so development and the test suite need neither Fleek nor a
Cloudflare account.
"""

from __future__ import annotations

import time

from fastapi import HTTPException, status

from app.config import get_settings
from app.services.fleek_signed import FleekUnavailable
from app.services.fleek_signed import request as fleek_request

MESSAGE = "Please complete the check and try again."
NOT_COMPLETED = "The check could not be completed. Try again."
VERIFY_PATH = "/api/internal/metal-erp/captcha/verify"
SITE_KEY_PATH = "/api/internal/metal-erp/captcha/site-key"
SITE_KEY_TTL_SECONDS = 300

_site_key_cache: tuple[float, str | None] | None = None


def _production() -> bool:
    return get_settings().app_env == "production"


def verify(token: str | None, ip: str | None) -> None:
    """Raises HTTPException: 400 bad or missing token; 503 not available (production only)."""
    try:
        code, body = fleek_request("POST", VERIFY_PATH, {"token": token or "", "ip": ip or ""})
    except FleekUnavailable:
        code, body = 0, {}
    if code == 200 and body.get("configured") is False:
        if _production():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-up is not available right now")
        return
    if code != 200:
        if _production():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_COMPLETED)
        return
    if body.get("unavailable"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_COMPLETED)
    if not body.get("success"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MESSAGE)


def site_key() -> str | None:
    """The public widget key from Fleek, cached a few minutes. None = no widget (not set, or Fleek down)."""
    global _site_key_cache
    now = time.monotonic()
    if _site_key_cache is not None and now - _site_key_cache[0] < SITE_KEY_TTL_SECONDS:
        return _site_key_cache[1]
    try:
        code, body = fleek_request("GET", SITE_KEY_PATH)
    except FleekUnavailable:
        return None  # not cached: ask again next time
    key = body.get("site_key") if code == 200 else None
    key = key if isinstance(key, str) and key else None
    _site_key_cache = (now, key)
    return key


def reset_cache() -> None:
    global _site_key_cache
    _site_key_cache = None

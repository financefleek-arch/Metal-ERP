"""Signed, cacheable URLs for catalog photos.

The SPA authenticates with a bearer header, which an `<img src>` cannot send.
So the (authenticated) items list hands out URLs carrying an HMAC over the item
id and an expiry; the image route verifies the signature and serves the bytes.
Photos are content-addressed, so the response is safely cacheable. The expiry is
rounded to the hour so a URL is stable within an hour and browsers can cache it.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.config import get_settings

_HOUR = 3600


def _sig(item_id: str, exp: int) -> str:
    key = get_settings().jwt_secret.encode()
    msg = f"catalog-image:{item_id}:{exp}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def sign_query(item_id: str, now: float | None = None) -> str:
    """`e=<expiry>&s=<signature>` for the image URL of `item_id`."""
    t = time.time() if now is None else now
    exp = (int(t) // _HOUR + 2) * _HOUR  # valid for 1 to 2 hours
    return f"e={exp}&s={_sig(item_id, exp)}"


def verify(item_id: str, exp: int, sig: str, now: float | None = None) -> bool:
    t = time.time() if now is None else now
    if exp < t:
        return False
    return hmac.compare_digest(sig, _sig(item_id, exp))

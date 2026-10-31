"""Where a sign-up came from: the facts, cleaned (Fleek admin Follow-ups shows them; docs/PLAN-signup-followup-console.md).

The page sends what it read from the landing URL (the same keys as Fleek's own landing page, services/crm_lead_intake.py there); the server adds what it
can tell from the User-Agent header. Everything is cleaned to a fixed shape and stored as given; nothing here decides the *source*, Fleek does that so the
rule can change without a Metal ERP deploy. Never kept: the raw User-Agent, an IP address, a full referrer URL.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

_TAGS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
_TAG = re.compile(r"^[A-Za-z0-9 _.+:/-]{1,80}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,100}$")
_LANDING = re.compile(r"^[A-Za-z0-9/_#.-]{1,80}$")  # path and #anchor only, never a query string (that is where the click id sits)
_DEVICES = ("mobile", "tablet", "desktop")
FIRST_SEEN_MAX_AGE = timedelta(days=8)  # the page keeps first-touch for 7 days


def _iso(raw: Any, now: datetime) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return dt.isoformat() if now - FIRST_SEEN_MAX_AGE <= dt <= now + timedelta(minutes=5) else None


def _host(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", raw.strip().lower()).split("/")[0].split("?")[0].split(":")[0]
    return host if _HOST.match(host) else None


def describe_agent(user_agent: str | None) -> dict[str, str]:
    """Coarse os / browser / in-app label from a User-Agent. Anything not recognised is left out, not guessed."""
    ua = user_agent or ""
    out: dict[str, str] = {}
    if "Android" in ua:
        out["os"] = "Android"
    elif re.search(r"iPhone|iPad|iPod", ua):
        out["os"] = "iOS"
    elif "Windows" in ua:
        out["os"] = "Windows"
    elif "Macintosh" in ua:
        out["os"] = "Mac"
    elif "Linux" in ua:
        out["os"] = "Linux"

    if "Instagram" in ua:
        out["in_app"] = "instagram"
    elif re.search(r"FBAN|FBAV|FB_IAB", ua):
        out["in_app"] = "facebook"
    elif "WhatsApp" in ua:
        out["in_app"] = "whatsapp"
    elif re.search(r"\bGSA/", ua):
        out["in_app"] = "google_app"
    elif "; wv)" in ua or (out.get("os") == "iOS" and "Safari/" not in ua and "CriOS" not in ua):
        out["in_app"] = "webview"  # some other app's built-in browser (Android WebView, or an iPhone page with no Safari token)

    if "Edg/" in ua or "EdgA/" in ua:
        out["browser"] = "Edge"
    elif "SamsungBrowser" in ua:
        out["browser"] = "Samsung"
    elif "OPR/" in ua or "Opera" in ua:
        out["browser"] = "Opera"
    elif "Firefox" in ua or "FxiOS" in ua:
        out["browser"] = "Firefox"
    elif "CriOS" in ua or "Chrome/" in ua:
        out["browser"] = "Chrome"
    elif "Safari/" in ua:
        out["browser"] = "Safari"
    return out


def clean(raw: Any, user_agent: str | None, now: datetime | None = None) -> dict[str, str] | None:
    """The attribution stored on a sign-up request: only the fields below survive; None when there is nothing at all."""
    now = now or datetime.now(UTC)
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for key in _TAGS:
            v = raw.get(key)
            if isinstance(v, str) and _TAG.match(v.strip()):
                out[key] = v.strip()
        host = _host(raw.get("referrer_host"))
        if host:
            out["referrer_host"] = host
        if raw.get("device") in _DEVICES:
            out["device"] = raw["device"]
        landing = raw.get("landing")
        if isinstance(landing, str) and _LANDING.match(landing):
            out["landing"] = landing
        seen = _iso(raw.get("first_seen_at"), now)
        if seen:
            out["first_seen_at"] = seen
    out.update(describe_agent(user_agent))
    return out or None

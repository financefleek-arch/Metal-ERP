"""Where a sign-up came from: cleaning, the User-Agent reading, and the lead feed carrying it (services/attribution.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import SignupRequest
from app.services import attribution, lead_feed

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)

ANDROID_CHROME = "Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36"
ANDROID_WEBVIEW = "Mozilla/5.0 (Linux; Android 13; SM-A135F; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/118.0.0.0 Mobile Safari/537.36"
IPHONE_SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
IPHONE_BARE_WEBVIEW = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
IPHONE_INSTAGRAM = IPHONE_BARE_WEBVIEW + " Instagram 300.0.0.0"
IPHONE_FACEBOOK = IPHONE_BARE_WEBVIEW + " [FBAN/FBIOS;FBAV/450.0]"


def test_agent_reading() -> None:
    assert attribution.describe_agent(ANDROID_CHROME) == {"os": "Android", "browser": "Chrome"}
    assert attribution.describe_agent(ANDROID_WEBVIEW)["in_app"] == "webview"
    assert attribution.describe_agent(IPHONE_SAFARI) == {"os": "iOS", "browser": "Safari"}
    assert attribution.describe_agent(IPHONE_BARE_WEBVIEW)["in_app"] == "webview"  # an iPhone page with no Safari token is another app's browser
    assert attribution.describe_agent(IPHONE_INSTAGRAM)["in_app"] == "instagram"
    assert attribution.describe_agent(IPHONE_FACEBOOK)["in_app"] == "facebook"
    assert attribution.describe_agent(None) == {} and attribution.describe_agent("curl/8") == {}


def test_clean_keeps_only_known_fields_and_adds_the_agent() -> None:
    seen = (NOW - timedelta(minutes=3)).isoformat()
    got = attribution.clean(
        {
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "metal<script>",  # rejected: not in the allowed alphabet
            "referrer_host": "https://l.instagram.com/some/path?x=1",
            "device": "mobile",
            "landing": "/#invoices",
            "first_seen_at": seen,
            "evil": "x",
            "ad_click_id": "ignored-here",
        },
        ANDROID_CHROME,
        NOW,
    )
    assert got == {
        "utm_source": "google",
        "utm_medium": "cpc",
        "referrer_host": "l.instagram.com",
        "device": "mobile",
        "landing": "/#invoices",
        "first_seen_at": datetime.fromisoformat(seen).isoformat(),
        "os": "Android",
        "browser": "Chrome",
    }


def test_clean_drops_bad_values_and_is_none_when_empty() -> None:
    bad = {
        "landing": "/?gclid=abc",  # a query string never goes in
        "device": "toaster",
        "first_seen_at": (NOW - timedelta(days=30)).isoformat(),  # older than the page keeps it
        "referrer_host": "not a host",
    }
    assert attribution.clean(bad, None, NOW) is None
    assert attribution.clean("nope", None, NOW) is None
    assert attribution.clean(None, IPHONE_SAFARI, NOW) == {"os": "iOS", "browser": "Safari"}


def test_the_feed_carries_the_first_attribution_of_the_lead() -> None:
    def add(minutes_ago: int, attr: dict | None) -> None:
        at = NOW - timedelta(minutes=minutes_ago)
        with SessionLocal() as s:
            r = SignupRequest(
                firm_name="Ravi Store", owner_name="Ravi", mobile="+919812345678", terms_version="1", terms_accepted_at=at,
                status="pending", expires_at=at + timedelta(minutes=30), attribution=attr,
            )
            s.add(r)
            s.flush()
            r.created_at = at
            s.commit()

    add(60, {"utm_source": "instagram", "os": "iOS"})
    add(30, {"utm_source": "later"})
    with SessionLocal() as s:
        (lead,) = lead_feed.build(s, NOW)["leads"]
    assert lead["attribution"] == {"utm_source": "instagram", "os": "iOS"}  # first touch, not the latest

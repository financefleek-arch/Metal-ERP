"""The sign-up lead feed (docs/PLAN-signup-followup-console.md): one lead per mobile, facts only, nothing secret."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import AuthAttempt, OtpChallenge, SignupRequest
from app.services import lead_feed

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


def _request(mobile: str, *, name: str = "Ravi", firm: str = "Ravi Store", minutes_ago: int = 30, status: str = "pending") -> str:
    at = NOW - timedelta(minutes=minutes_ago)
    with SessionLocal() as s:
        r = SignupRequest(
            firm_name=firm,
            owner_name=name,
            mobile=mobile,
            password_hash="x" * 20,
            terms_version="1",
            terms_accepted_at=at,
            status=status,
            expires_at=at + timedelta(minutes=30),
        )
        s.add(r)
        s.flush()
        s.add(
            OtpChallenge(
                purpose="signup",
                subject_id=r.id,
                channel="whatsapp",
                target=mobile,
                code_hash="h" * 64,
                expires_at=at + timedelta(minutes=10),
                attempts=2,
            )
        )
        r.created_at = at
        s.commit()
        return r.id


def _fail(key: str, n: int, minutes_ago: int = 10) -> None:
    with SessionLocal() as s:
        for _ in range(n):
            s.add(AuthAttempt(kind="login_email", key=key, created_at=NOW - timedelta(minutes=minutes_ago)))
        s.commit()


def _build() -> dict:
    with SessionLocal() as s:
        return lead_feed.build(s, NOW)


def test_a_sign_up_is_one_lead_with_its_code_facts() -> None:
    _request("+919876543210")
    body = _build()
    assert body["version"] == 1
    (lead,) = body["leads"]
    assert lead["key"] == lead["contact"] == "+919876543210" and lead["kind"] == "mobile"
    assert lead["name"] == "Ravi" and lead["firm"] == "Ravi Store" and lead["has_account"] is False
    (signup,) = lead["signups"]
    assert signup["status"] == "pending"
    assert signup["codes"] == [
        {"channel": "whatsapp", "sent_at": signup["codes"][0]["sent_at"], "attempts": 2, "used": False}
    ]


def test_a_second_attempt_by_the_same_mobile_joins_the_same_lead() -> None:
    _request("+919876543210", minutes_ago=90, status="cancelled")
    _request("+919876543210", name="Ravi K", minutes_ago=30)
    (lead,) = _build()["leads"]
    assert [s["status"] for s in lead["signups"]] == ["cancelled", "pending"]
    assert lead["name"] == "Ravi K"  # the latest attempt wins


def test_failed_sign_ins_become_a_lead_without_an_ip_and_junk_is_dropped() -> None:
    _fail("+917667004931", 3)
    _fail("someone@example.com", 2)
    _fail("not a number", 4)
    leads = {x["key"]: x for x in _build()["leads"]}
    assert set(leads) == {"+917667004931", "someone@example.com"}
    assert leads["+917667004931"]["login_fails"] == 3 and leads["+917667004931"]["signups"] == []
    assert leads["someone@example.com"]["kind"] == "email"


def test_the_feed_carries_no_secret() -> None:
    _request("+919876543210")
    text = str(_build())
    assert "hhhhhhhh" not in text  # the code hash
    assert "xxxxxxxx" not in text  # the password hash
    assert "password" not in text and "client_ip" not in text


def test_an_old_lead_is_left_out() -> None:
    _request("+919876543210", minutes_ago=60 * 24 * (lead_feed.LEAD_DAYS + 2))
    assert _build()["leads"] == []


def test_an_unchanged_feed_is_not_sent_twice_but_a_change_is(monkeypatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(lead_feed, "fleek_request", lambda m, p, body: (sent.append(body) or (204, {})))
    monkeypatch.setattr(lead_feed, "_last_sent", {"digest": None, "at": 0.0})
    monkeypatch.setattr(lead_feed, "build", lambda session: {"version": 1, "as_of": "t1", "leads": [{"key": "a"}]})
    assert lead_feed.send() == 1
    assert lead_feed.send() == 0  # nothing changed
    monkeypatch.setattr(lead_feed, "build", lambda session: {"version": 1, "as_of": "t2", "leads": [{"key": "a"}, {"key": "b"}]})
    assert lead_feed.send() == 2
    assert len(sent) == 2

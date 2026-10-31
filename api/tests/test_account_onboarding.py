"""Verified sign-up, sign-in limits, revocable sessions, password reset, the Free plan gate and the Setup data
(docs/PLAN-onboarding-and-account.md). Fleek is faked at the one function every call goes through."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import OtpChallenge, SignupRequest, Tenant, User
from app.services import account, captcha
from app.services.fleek_signed import FleekUnavailable


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class FakeFleek:
    """Records what Metal ERP asks Fleek to carry or create, and answers like Fleek."""

    def __init__(self) -> None:
        self.whatsapp: list[tuple[str, str]] = []
        self.email: list[tuple[str, str, str]] = []
        self.signups: dict[str, str] = {}
        self.signup_payloads: list[dict[str, Any]] = []
        self.feature_requests: list[dict[str, Any]] = []
        self.down = False
        self.signup_status = 201
        # Fleek's CAPTCHA answer: None = no key set in Fleek; else {"success": bool, ...}
        self.captcha: dict[str, Any] | None = None
        self.site_key: str | None = None
        self.captcha_tokens: list[str] = []

    def __call__(
        self, method: str, path: str, payload: dict[str, Any] | None = None, **_: Any
    ) -> tuple[int, dict[str, Any]]:
        if self.down:
            raise FleekUnavailable("down")
        if path.endswith("/captcha/site-key"):
            return 200, {"site_key": self.site_key}
        assert payload is not None
        if path.endswith("/captcha/verify"):
            self.captcha_tokens.append(payload["token"])
            if self.captcha is None:
                return 200, {"configured": False}
            return 200, {"configured": True, **self.captcha}
        if path.endswith("/codes/whatsapp"):
            self.whatsapp.append((payload["to"], payload["code"]))
            return 200, {"status": "sent"}
        if path.endswith("/codes/email"):
            self.email.append((payload["to"], payload["code"], payload["purpose"]))
            return 200, {"status": "sent"}
        if path.endswith("/self-signup"):
            if self.signup_status != 201:
                return self.signup_status, {"error": "email_in_use"}
            self.signup_payloads.append(payload)
            org = self.signups.setdefault(payload["mobile"], f"org-{len(self.signups) + 1}")
            return 201, {"org_id": org}
        if path.endswith("/feature-requests"):
            self.feature_requests.append(payload)
            return 201, {"status": "received"}
        raise AssertionError(path)

    def last_code(self, channel: str, purpose: str | None = None) -> str:
        if channel == "whatsapp":
            return self.whatsapp[-1][1]
        rows = [r for r in self.email if purpose is None or r[2] == purpose]
        return rows[-1][1]


@pytest.fixture
def fleek(monkeypatch: pytest.MonkeyPatch) -> FakeFleek:
    f = FakeFleek()
    monkeypatch.setattr("app.services.account.fleek_request", f)
    monkeypatch.setattr("app.routers.onboarding.fleek_request", f)
    monkeypatch.setattr("app.services.captcha.fleek_request", f)
    captcha.reset_cache()
    return f


SIGNUP = {
    "firm_name": "Sethia Metal Store",
    "owner_name": "Ravi Sethia",
    "email": "owner@sethia.example.com",
    "mobile": "98765 43210",
    "password": "s3cret-pass",
    "accept_terms": True,
}
MOBILE = "+919876543210"


def _start(client: TestClient, **over: Any) -> Any:
    return client.post("/api/auth/signup/start", json={**SIGNUP, **over})


def _signup(client: TestClient, fleek: FakeFleek, **over: Any) -> str:
    r = _start(client, **over)
    assert r.status_code == 200, r.text
    v = client.post(
        "/api/auth/signup/verify",
        json={"request_id": r.json()["request_id"], "code": fleek.last_code("whatsapp")},
    )
    assert v.status_code == 201, v.text
    return v.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------------------- sign-up


def test_signup_creates_a_linked_free_firm_only_after_the_whatsapp_code(
    client: TestClient, fleek: FakeFleek
) -> None:
    r = _start(client)
    assert r.status_code == 200
    body = r.json()
    assert body["mobile_masked"].startswith("+91 98") and body["mobile_masked"].endswith("10")
    assert "email_masked" not in body
    assert fleek.whatsapp[0][0] == "+919876543210"
    assert fleek.email == []  # one code, to WhatsApp only
    with SessionLocal() as s:  # nothing exists yet: no firm, no user
        assert s.scalar(select(Tenant)) is None
        assert s.scalar(select(User)) is None

    v = client.post(
        "/api/auth/signup/verify",
        json={"request_id": body["request_id"], "code": fleek.last_code("whatsapp")},
    )
    assert v.status_code == 201
    with SessionLocal() as s:
        tenant = s.scalar(select(Tenant))
        user = s.scalar(select(User))
        assert tenant is not None and user is not None
        assert tenant.fleek_org_id == "org-1" and tenant.signup_source == "self"
        assert user.mobile == "+919876543210"
        assert user.mobile_verified_at is not None and user.email_verified_at is None  # the email is never verified
        assert user.email == "owner@sethia.example.com"
        assert user.terms_accepted_at is not None

    me = client.get("/api/auth/me", headers=_h(v.json()["access_token"])).json()
    assert me["plan_tier"] == "free"
    assert me["mobile_verified"] and not me["email_verified"] and me["firm_name"] == "Sethia Metal Store"
    assert me["label"] == "owner@sethia.example.com"
    assert me["features"]["slip"] is False and me["features"]["import"] is False


def test_a_wrong_code_makes_no_firm_and_burns_after_five_tries(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    for _ in range(5):
        r = client.post(
            "/api/auth/signup/verify",
            json={"request_id": rid, "code": "000000"},
        )
        assert r.status_code == 400
    # even the right code no longer works once the tries are used up
    r = client.post(
        "/api/auth/signup/verify",
        json={"request_id": rid, "code": fleek.last_code("whatsapp")},
    )
    assert r.status_code == 400
    with SessionLocal() as s:
        assert s.scalar(select(Tenant)) is None


def test_fleek_down_makes_no_firm_and_a_retry_succeeds(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    codes = {"request_id": rid, "code": fleek.last_code("whatsapp")}
    fleek.down = True
    assert client.post("/api/auth/signup/verify", json=codes).status_code == 503
    with SessionLocal() as s:
        assert s.scalar(select(Tenant)) is None
        req = s.get(SignupRequest, rid)
        assert req is not None and req.status == "pending"
    fleek.down = False
    assert client.post("/api/auth/signup/verify", json=codes).status_code == 201


def test_a_second_verify_does_not_make_a_second_firm(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    codes = {"request_id": rid, "code": fleek.last_code("whatsapp")}
    assert client.post("/api/auth/signup/verify", json=codes).status_code == 201
    assert client.post("/api/auth/signup/verify", json=codes).status_code == 409
    with SessionLocal() as s:
        assert len(s.scalars(select(Tenant)).all()) == 1


def test_an_existing_mobile_is_refused_but_a_shared_email_is_not(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    by_mobile = _start(client, email="other@sethia.example.com")
    assert by_mobile.status_code == 409 and by_mobile.json()["detail"] == account.CLASH
    # the email is only contact detail, so a second firm may give the same one (or none)
    assert _start(client, mobile="9123456780").status_code == 200
    assert _start(client, mobile="9123456781", email=None).status_code == 200


def test_signup_needs_no_email_and_sends_no_email_code(client: TestClient, fleek: FakeFleek) -> None:
    body = {k: v for k, v in SIGNUP.items() if k != "email"}
    r = client.post("/api/auth/signup/start", json=body)
    assert r.status_code == 200, r.text
    v = client.post(
        "/api/auth/signup/verify", json={"request_id": r.json()["request_id"], "code": fleek.last_code("whatsapp")}
    )
    assert v.status_code == 201, v.text
    assert fleek.email == [] and fleek.signup_payloads[-1]["email"] == ""  # Fleek is told there is none
    with SessionLocal() as s:
        user = s.scalar(select(User))
        assert user is not None and user.email is None and user.mobile == MOBILE
    me = client.get("/api/auth/me", headers=_h(v.json()["access_token"])).json()
    assert me["email"] is None and me["label"] == MOBILE


def test_signup_keeps_the_google_ads_click_id_and_refuses_a_junk_one(client: TestClient, fleek: FakeFleek) -> None:
    ok = _start(client, ad_click_kind="gclid", ad_click_id="Cj0KCQ_abc-123")
    assert ok.status_code == 200, ok.text
    with SessionLocal() as s:
        req = s.scalar(select(SignupRequest))
        assert req is not None and (req.ad_click_kind, req.ad_click_id) == ("gclid", "Cj0KCQ_abc-123")
    assert _start(client, ad_click_kind="gclid", ad_click_id="x'; drop table").status_code == 422
    assert _start(client, ad_click_kind="utm", ad_click_id="abc").status_code == 422
    plain = _start(client, mobile="9876500001")
    assert plain.status_code == 200  # no click id is the normal case


def test_signup_needs_terms_a_real_mobile_and_a_decent_password(client: TestClient, fleek: FakeFleek) -> None:
    assert _start(client, accept_terms=False).status_code == 422
    assert _start(client, mobile="12345").status_code == 422
    assert _start(client, mobile="5876543210").status_code == 422  # Indian mobiles start 6-9
    assert _start(client, password="abcdefgh").status_code == 422  # no digit or special
    assert _start(client, password="password123").status_code == 422  # too common
    assert _start(client, password="9876543210").status_code == 422  # their own mobile
    assert fleek.whatsapp == []  # nothing was sent for any of them


def test_resend_respects_the_one_minute_gap(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    r = client.post("/api/auth/signup/resend", json={"request_id": rid})
    assert r.status_code == 429


def test_captcha_checked_by_fleek_blocks_a_bad_token_and_sends_nothing(
    client: TestClient, fleek: FakeFleek
) -> None:
    fleek.captcha = {"success": False}
    r = _start(client, captcha_token="bad")
    assert r.status_code == 400 and fleek.captcha_tokens == ["bad"]
    assert fleek.whatsapp == [] and fleek.email == []
    fleek.captcha = {"success": True}
    assert _start(client, captcha_token="good").status_code == 200


def test_captcha_outage_is_a_retry_not_a_pass(client: TestClient, fleek: FakeFleek) -> None:
    fleek.captcha = {"success": False, "unavailable": True}
    assert _start(client, captcha_token="t").status_code == 503
    assert fleek.whatsapp == []


def test_captcha_refuses_in_production_when_fleek_has_no_key_or_is_down(
    client: TestClient, fleek: FakeFleek, monkeypatch: pytest.MonkeyPatch
) -> None:
    class S:
        app_env = "production"

    monkeypatch.setattr(captcha, "get_settings", lambda: S())
    assert _start(client).status_code == 503  # Fleek answers "not configured"
    fleek.down = True
    assert _start(client).status_code == 503  # Fleek unreachable
    assert fleek.whatsapp == []


def test_captcha_skipped_outside_production_without_a_key(client: TestClient, fleek: FakeFleek) -> None:
    assert _start(client).status_code == 200


def test_forgot_password_is_behind_the_captcha_too(client: TestClient, fleek: FakeFleek) -> None:
    fleek.captcha = {"success": False}
    r = client.post("/api/auth/password/forgot", json={"identifier": MOBILE, "captcha_token": "x"})
    assert r.status_code == 400


def test_page_config_serves_the_site_key_from_fleek_cached(client: TestClient, fleek: FakeFleek) -> None:
    fleek.site_key = "0xSITE"
    assert client.get("/api/auth/config").json() == {"captcha_site_key": "0xSITE"}
    fleek.site_key = "changed"
    assert client.get("/api/auth/config").json() == {"captcha_site_key": "0xSITE"}  # cached for a few minutes
    captcha.reset_cache()
    fleek.down = True
    # Fleek down: no widget, and not an error
    assert client.get("/api/auth/config").json() == {"captcha_site_key": None}


def test_old_register_is_closed_unless_switched_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "registration_open", False)
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "X Metals", "email": "x@x.example.com", "password": "s3cret-pass"},
    )
    assert r.status_code == 404


# ----------------------------------------------------------------------------------------- sign-in


def test_login_locks_after_five_wrong_passwords_then_reset_clears_it(
    client: TestClient, fleek: FakeFleek
) -> None:
    _signup(client, fleek)
    bad = {"identifier": MOBILE, "password": "wrong-pass-1"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    assert client.post("/api/auth/login", json=bad).status_code == 429
    good = {"identifier": MOBILE, "password": SIGNUP["password"]}
    assert client.post("/api/auth/login", json=good).status_code == 429  # locked, even for the right one
    # another spelling of the same number is the same person, so the lock holds
    assert client.post("/api/auth/login", json={**good, "identifier": "98765 43210"}).status_code == 429


def test_a_good_login_forgets_earlier_slips(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    for _ in range(3):
        client.post("/api/auth/login", json={"identifier": MOBILE, "password": "nope-nope-1"})
    good = {"identifier": MOBILE, "password": SIGNUP["password"]}
    assert client.post("/api/auth/login", json=good).status_code == 200
    for _ in range(4):  # the three slips no longer count against the five
        client.post("/api/auth/login", json={"identifier": MOBILE, "password": "nope-nope-1"})
    assert client.post("/api/auth/login", json=good).status_code == 200


def test_login_takes_the_mobile_in_any_spelling_and_an_older_account_its_email(
    client: TestClient, fleek: FakeFleek
) -> None:
    _signup(client, fleek)
    pw = SIGNUP["password"]
    for who in (MOBILE, "9876543210", "98765 43210", "+91 98765-43210", "09876543210"):
        assert client.post("/api/auth/login", json={"identifier": who, "password": pw}).status_code == 200, who
    # accounts made before mobile sign-in (and Fleek operators) still sign in with the email
    assert client.post("/api/auth/login", json={"identifier": SIGNUP["email"], "password": pw}).status_code == 200
    # a client that has not updated still sends `email`
    assert client.post("/api/auth/login", json={"email": SIGNUP["email"], "password": pw}).status_code == 200
    bad = client.post("/api/auth/login", json={"identifier": "9123456780", "password": pw})
    assert bad.status_code == 401 and bad.json()["detail"] == "Incorrect mobile number or password"
    assert client.post("/api/auth/login", json={"identifier": "abc", "password": pw}).status_code == 401


# ----------------------------------------------------------------------------- sessions, passwords


def test_change_password_ends_other_sessions_and_keeps_this_one(
    client: TestClient, fleek: FakeFleek
) -> None:
    old = _signup(client, fleek)
    r = client.post(
        "/api/auth/change-password",
        headers=_h(old),
        json={"current_password": SIGNUP["password"], "new_password": "n3w-secret-pw"},
    )
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=_h(old)).status_code == 401
    assert client.get("/api/auth/me", headers=_h(r.json()["access_token"])).status_code == 200
    login = {"identifier": MOBILE, "password": "n3w-secret-pw"}
    assert client.post("/api/auth/login", json=login).status_code == 200


def test_change_password_checks_the_current_one_and_the_rules(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    path = "/api/auth/change-password"
    r = client.post(path, headers=_h(t), json={"current_password": "wrong", "new_password": "n3w-secret-pw"})
    assert r.status_code == 400
    r = client.post(
        path, headers=_h(t), json={"current_password": SIGNUP["password"], "new_password": "abcdefgh"}
    )
    assert r.status_code == 422


def test_sign_out_others_keeps_only_the_fresh_token(client: TestClient, fleek: FakeFleek) -> None:
    old = _signup(client, fleek)
    fresh = client.post("/api/auth/sign-out-others", headers=_h(old)).json()["access_token"]
    assert client.get("/api/auth/me", headers=_h(old)).status_code == 401
    assert client.get("/api/auth/me", headers=_h(fresh)).status_code == 200


def test_a_password_set_by_someone_else_must_be_replaced_first(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    with SessionLocal() as s:
        u = s.scalar(select(User))
        assert u is not None
        u.must_change_password = True
        s.commit()
    assert client.get("/api/parties", headers=_h(t)).status_code == 403
    assert client.get("/api/auth/me", headers=_h(t)).json()["must_change_password"] is True
    r = client.post(
        "/api/auth/change-password",
        headers=_h(t),
        json={"current_password": SIGNUP["password"], "new_password": "n3w-secret-pw"},
    )
    assert client.get("/api/parties", headers=_h(r.json()["access_token"])).status_code == 200


def test_forgot_password_is_silent_for_unknown_mobiles_and_resets_for_real_ones(
    client: TestClient, fleek: FakeFleek
) -> None:
    old = _signup(client, fleek)
    n = len(fleek.whatsapp)
    r = client.post("/api/auth/password/forgot", json={"identifier": "9123456780"})
    assert r.status_code == 204 and len(fleek.whatsapp) == n
    r = client.post("/api/auth/password/forgot", json={"identifier": "not a number"})
    assert r.status_code == 204 and len(fleek.whatsapp) == n

    r = client.post("/api/auth/password/forgot", json={"identifier": "98765 43210"})
    assert r.status_code == 204 and len(fleek.whatsapp) == n + 1 and fleek.whatsapp[-1][0] == MOBILE
    code = fleek.last_code("whatsapp")

    bad = client.post(
        "/api/auth/password/reset",
        json={"identifier": MOBILE, "code": "000000", "new_password": "n3w-secret-pw"},
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/password/reset",
        json={"identifier": MOBILE, "code": code, "new_password": "n3w-secret-pw"},
    )
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=_h(old)).status_code == 401
    # the code is single use
    again = client.post(
        "/api/auth/password/reset",
        json={"identifier": MOBILE, "code": code, "new_password": "an0ther-secret"},
    )
    assert again.status_code == 400


def test_an_older_account_with_no_mobile_resets_by_email(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    with SessionLocal() as s:  # an account from before mobile sign-in: an email and no mobile
        u = s.scalar(select(User))
        assert u is not None
        u.mobile, u.mobile_verified_at = None, None
        s.commit()
    n = len(fleek.whatsapp)
    r = client.post("/api/auth/password/forgot", json={"identifier": SIGNUP["email"]})
    assert r.status_code == 204 and len(fleek.whatsapp) == n and fleek.email[-1][2] == "reset"
    ok = client.post(
        "/api/auth/password/reset",
        json={
            "identifier": SIGNUP["email"],
            "code": fleek.last_code("email", "reset"),
            "new_password": "n3w-secret-pw",
        },
    )
    assert ok.status_code == 200


def test_a_mobile_typed_by_an_operator_is_proved_by_the_first_whatsapp_reset(
    client: TestClient, fleek: FakeFleek
) -> None:
    _signup(client, fleek)
    with SessionLocal() as s:
        u = s.scalar(select(User))
        assert u is not None
        u.mobile_verified_at = None
        s.commit()
    client.post("/api/auth/password/forgot", json={"identifier": MOBILE})
    client.post(
        "/api/auth/password/reset",
        json={"identifier": MOBILE, "code": fleek.last_code("whatsapp"), "new_password": "n3w-secret-pw"},
    )
    with SessionLocal() as s:
        u = s.scalar(select(User))
        assert u is not None and u.mobile_verified_at is not None


def test_codes_are_stored_hashed(client: TestClient, fleek: FakeFleek) -> None:
    _start(client)
    with SessionLocal() as s:
        for row in s.scalars(select(OtpChallenge)).all():
            assert row.code_hash not in {c for _, c in fleek.whatsapp} | {c for _, c, _ in fleek.email}
            assert len(row.code_hash) == 64


# --------------------------------------------------------------------------------- the Free plan


def test_free_firm_is_held_back_from_paid_features_but_can_take_payments(
    client: TestClient, fleek: FakeFleek
) -> None:
    t = _signup(client, fleek)
    for method, path in [
        ("get", "/api/reminders"),
        ("get", "/api/collections"),
        ("post", "/api/parties/import"),
        ("post", "/api/invoices/from-slip"),
        ("post", "/api/invoices/voice-line"),
        ("get", "/api/whatsapp-integrations"),
        ("post", "/api/item-tally/push"),
    ]:
        r = getattr(client, method)(path, headers=_h(t))
        assert r.status_code == 403, (path, r.status_code)
        assert r.json()["detail"]["code"] == "not_on_plan", path
    # core billing is open: these may fail validation, but never with "not on your plan"
    assert client.get("/api/parties", headers=_h(t)).status_code == 200
    assert client.get("/api/items", headers=_h(t)).status_code == 200
    assert client.get("/api/invoices", headers=_h(t)).status_code == 200
    r = client.post("/api/payments", headers=_h(t), json={})
    assert r.status_code == 422


def test_a_firm_that_never_went_through_sign_up_keeps_every_feature(client: TestClient) -> None:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Old Firm", "email": "old@firm.example.com", "password": "s3cret-pass"},
    )
    t = r.json()["access_token"]
    assert client.get("/api/auth/me", headers=_h(t)).json()["plan_tier"] == "full"
    assert client.get("/api/reminders", headers=_h(t)).status_code != 403
    assert client.get("/api/collections", headers=_h(t)).status_code == 200


def test_a_paid_plan_from_fleek_lifts_the_limits(client: TestClient, fleek: FakeFleek) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models import TenantEntitlement

    t = _signup(client, fleek)
    now = datetime.now(UTC)
    with SessionLocal() as s:
        tenant = s.scalar(select(Tenant))
        assert tenant is not None
        s.add(
            TenantEntitlement(
                tenant_id=tenant.id,
                payload={
                    "status": "active",
                    "plan": {"key": "metal_erp_starter", "label": "Starter"},
                    "limits": {"users": 3},
                },
                last_verified_at=now,
                valid_until=now + timedelta(hours=12),
            )
        )
        s.commit()
    assert client.get("/api/auth/me", headers=_h(t)).json()["plan_tier"] == "full"
    assert client.get("/api/collections", headers=_h(t)).status_code == 200


# ------------------------------------------------------------------------------------------ Setup


def test_setup_checklist_follows_the_firm_data(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    ov = client.get("/api/onboarding", headers=_h(t)).json()
    assert ov["done"] == 0 and ov["total"] == 5 and ov["plan_tier"] == "free"
    assert [s["key"] for s in ov["steps"]] == ["firm", "invoice", "pdf", "payment", "bank"]
    assert {c["key"]: c["status"] for c in ov["features"]}["whatsapp"] == "locked"

    client.patch("/api/tenant", headers=_h(t), json={"address": "12 Main Road", "state_code": "27"})
    ov = client.get("/api/onboarding", headers=_h(t)).json()
    done = {s["key"]: s["done"] for s in ov["steps"]}
    assert done["firm"] and not done["invoice"] and not done["pdf"] and not done["payment"] and not done["bank"]


def test_owner_can_ask_fleek_for_a_feature_once_fleek_has_heard(
    client: TestClient, fleek: FakeFleek
) -> None:
    t = _signup(client, fleek)
    r = client.post("/api/onboarding/requests", headers=_h(t), json={"feature": "whatsapp"})
    assert r.status_code == 200
    assert fleek.feature_requests[0]["feature"] == "whatsapp"
    ov = client.get("/api/onboarding", headers=_h(t)).json()
    assert {c["key"]: c["status"] for c in ov["features"]}["whatsapp"] == "requested"

    fleek.down = True
    r = client.post("/api/onboarding/requests", headers=_h(t), json={"feature": "tally"})
    assert r.status_code == 503  # not recorded as requested when Fleek was not told
    ov = client.get("/api/onboarding", headers=_h(t)).json()
    assert {c["key"]: c["status"] for c in ov["features"]}["tally"] == "locked"
    assert client.post("/api/onboarding/requests", headers=_h(t), json={"feature": "nope"}).status_code == 404


def test_setup_can_be_closed_and_reopened(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    client.put("/api/onboarding/dismissed", headers=_h(t), json={"dismissed": True})
    assert client.get("/api/onboarding", headers=_h(t)).json()["dismissed"] is True
    client.put("/api/onboarding/dismissed", headers=_h(t), json={"dismissed": False})
    assert client.get("/api/onboarding", headers=_h(t)).json()["dismissed"] is False


# ------------------------------------------------------------------------------- short sign-up


SHORT = {"firm_name": "Sethia Metal Store", "mobile": "98765 43210", "accept_terms": True}


def _short_account(client: TestClient, fleek: FakeFleek) -> str:
    r = client.post("/api/auth/signup/start", json=SHORT)
    assert r.status_code == 200, r.text
    v = client.post(
        "/api/auth/signup/verify", json={"request_id": r.json()["request_id"], "code": fleek.last_code("whatsapp")}
    )
    assert v.status_code == 201, v.text
    return v.json()["access_token"]


def test_a_short_signup_needs_only_a_firm_and_a_mobile_and_has_no_password_yet(
    client: TestClient, fleek: FakeFleek
) -> None:
    token = _short_account(client, fleek)
    assert fleek.signup_payloads[-1]["owner_name"] == "Sethia Metal Store"  # the firm stands in for the name
    me = client.get("/api/auth/me", headers=_h(token)).json()
    assert me["password_set"] is False and me["firm_name"] == "Sethia Metal Store"
    # nobody knows the placeholder password, so a password sign-in cannot succeed
    assert client.post("/api/auth/login", json={"identifier": MOBILE, "password": ""}).status_code in (401, 422)


def test_set_password_once_then_sign_in_with_it(client: TestClient, fleek: FakeFleek) -> None:
    token = _short_account(client, fleek)
    weak = client.post("/api/auth/set-password", json={"new_password": "short"}, headers=_h(token))
    assert weak.status_code == 422
    r = client.post(
        "/api/auth/set-password",
        json={"new_password": "s3cret-pass", "email": "Owner@Sethia.example.com"},
        headers=_h(token),
    )
    assert r.status_code == 200, r.text
    fresh = r.json()["access_token"]
    me = client.get("/api/auth/me", headers=_h(fresh)).json()
    assert me["password_set"] is True and me["email"] == "owner@sethia.example.com"
    assert client.get("/api/auth/me", headers=_h(token)).status_code == 401  # the old token ended
    assert client.post("/api/auth/login", json={"identifier": MOBILE, "password": "s3cret-pass"}).status_code == 200
    again = client.post("/api/auth/set-password", json={"new_password": "an0ther-pass"}, headers=_h(fresh))
    assert again.status_code == 409


def test_a_full_signup_is_unchanged_and_already_has_its_password(client: TestClient, fleek: FakeFleek) -> None:
    token = _signup(client, fleek)
    assert client.get("/api/auth/me", headers=_h(token)).json()["password_set"] is True
    assert client.post("/api/auth/set-password", json={"new_password": "an0ther-pass"}, headers=_h(token)).status_code == 409


def test_a_whatsapp_reset_counts_as_choosing_a_password(client: TestClient, fleek: FakeFleek) -> None:
    _short_account(client, fleek)
    assert client.post("/api/auth/password/forgot", json={"identifier": MOBILE}).status_code in (200, 202, 204)
    r = client.post(
        "/api/auth/password/reset",
        json={"identifier": MOBILE, "code": fleek.last_code("whatsapp"), "new_password": "n3w-secret-pw"},
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me", headers=_h(r.json()["access_token"])).json()["password_set"] is True


def test_call_back_request_is_kept_for_the_follow_up_feed(client: TestClient, fleek: FakeFleek) -> None:
    from app.services import lead_feed

    rid = client.post("/api/auth/signup/start", json=SHORT).json()["request_id"]
    assert client.post("/api/auth/signup/callback", json={"request_id": rid}).status_code == 204
    with SessionLocal() as s:
        req = s.scalar(select(SignupRequest))
        assert req is not None and req.callback_requested_at is not None
        lead = lead_feed.build(s)["leads"][0]
        assert lead["signups"][0]["callback_at"] is not None
    assert client.post("/api/auth/signup/callback", json={"request_id": "no-such"}).status_code == 410

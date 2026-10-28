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
        self.feature_requests: list[dict[str, Any]] = []
        self.down = False
        self.signup_status = 201

    def __call__(
        self, method: str, path: str, payload: dict[str, Any] | None = None, **_: Any
    ) -> tuple[int, dict[str, Any]]:
        if self.down:
            raise FleekUnavailable("down")
        assert payload is not None
        if path.endswith("/codes/whatsapp"):
            self.whatsapp.append((payload["to"], payload["code"]))
            return 200, {"status": "sent"}
        if path.endswith("/codes/email"):
            self.email.append((payload["to"], payload["code"], payload["purpose"]))
            return 200, {"status": "sent"}
        if path.endswith("/self-signup"):
            if self.signup_status != 201:
                return self.signup_status, {"error": "email_in_use"}
            org = self.signups.setdefault(payload["email"], f"org-{len(self.signups) + 1}")
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
    return f


SIGNUP = {
    "firm_name": "Sethia Metal Store",
    "owner_name": "Ravi Sethia",
    "email": "owner@sethia.example.com",
    "mobile": "98765 43210",
    "password": "s3cret-pass",
    "accept_terms": True,
}


def _start(client: TestClient, **over: Any) -> Any:
    return client.post("/api/auth/signup/start", json={**SIGNUP, **over})


def _signup(client: TestClient, fleek: FakeFleek, **over: Any) -> str:
    r = _start(client, **over)
    assert r.status_code == 200, r.text
    v = client.post(
        "/api/auth/signup/verify",
        json={
            "request_id": r.json()["request_id"],
            "whatsapp_code": fleek.last_code("whatsapp"),
            "email_code": fleek.last_code("email", "signup"),
        },
    )
    assert v.status_code == 201, v.text
    return v.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------------------- sign-up


def test_signup_creates_a_linked_free_firm_only_after_both_codes(
    client: TestClient, fleek: FakeFleek
) -> None:
    r = _start(client)
    assert r.status_code == 200
    body = r.json()
    assert body["mobile_masked"].startswith("+91 98") and body["mobile_masked"].endswith("10")
    assert "owner" not in body["email_masked"]
    assert fleek.whatsapp[0][0] == "+919876543210"
    with SessionLocal() as s:  # nothing exists yet: no firm, no user
        assert s.scalar(select(Tenant)) is None
        assert s.scalar(select(User)) is None

    v = client.post(
        "/api/auth/signup/verify",
        json={
            "request_id": body["request_id"],
            "whatsapp_code": fleek.last_code("whatsapp"),
            "email_code": fleek.last_code("email", "signup"),
        },
    )
    assert v.status_code == 201
    with SessionLocal() as s:
        tenant = s.scalar(select(Tenant))
        user = s.scalar(select(User))
        assert tenant is not None and user is not None
        assert tenant.fleek_org_id == "org-1" and tenant.signup_source == "self"
        assert user.mobile == "+919876543210"
        assert user.mobile_verified_at is not None and user.email_verified_at is not None
        assert user.terms_accepted_at is not None

    me = client.get("/api/auth/me", headers=_h(v.json()["access_token"])).json()
    assert me["plan_tier"] == "free"
    assert me["mobile_verified"] and me["email_verified"] and me["firm_name"] == "Sethia Metal Store"
    assert me["features"]["slip"] is False and me["features"]["import"] is False


def test_wrong_codes_make_no_firm_and_burn_after_five_tries(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    for _ in range(5):
        r = client.post(
            "/api/auth/signup/verify",
            json={"request_id": rid, "whatsapp_code": "000000", "email_code": "000000"},
        )
        assert r.status_code == 400
    # even the right codes no longer work once the tries are used up
    r = client.post(
        "/api/auth/signup/verify",
        json={
            "request_id": rid,
            "whatsapp_code": fleek.last_code("whatsapp"),
            "email_code": fleek.last_code("email", "signup"),
        },
    )
    assert r.status_code == 400
    with SessionLocal() as s:
        assert s.scalar(select(Tenant)) is None


def test_fleek_down_makes_no_firm_and_a_retry_succeeds(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    codes = {
        "request_id": rid,
        "whatsapp_code": fleek.last_code("whatsapp"),
        "email_code": fleek.last_code("email", "signup"),
    }
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
    codes = {
        "request_id": rid,
        "whatsapp_code": fleek.last_code("whatsapp"),
        "email_code": fleek.last_code("email", "signup"),
    }
    assert client.post("/api/auth/signup/verify", json=codes).status_code == 201
    assert client.post("/api/auth/signup/verify", json=codes).status_code == 409
    with SessionLocal() as s:
        assert len(s.scalars(select(Tenant)).all()) == 1


def test_existing_email_or_mobile_gets_one_generic_answer(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    by_email = _start(client, mobile="9123456780")
    by_mobile = _start(client, email="other@sethia.example.com")
    assert by_email.status_code == by_mobile.status_code == 409
    assert by_email.json()["detail"] == by_mobile.json()["detail"] == account.CLASH


def test_signup_needs_terms_a_real_mobile_and_a_decent_password(client: TestClient, fleek: FakeFleek) -> None:
    assert _start(client, accept_terms=False).status_code == 422
    assert _start(client, mobile="12345").status_code == 422
    assert _start(client, mobile="5876543210").status_code == 422  # Indian mobiles start 6-9
    assert _start(client, password="abcdefgh").status_code == 422  # no digit or special
    assert _start(client, password="password123").status_code == 422  # too common
    assert fleek.whatsapp == []  # nothing was sent for any of them


def test_resend_respects_the_one_minute_gap(client: TestClient, fleek: FakeFleek) -> None:
    rid = _start(client).json()["request_id"]
    r = client.post("/api/auth/signup/resend", json={"request_id": rid, "channel": "whatsapp"})
    assert r.status_code == 429


def test_captcha_is_required_when_a_secret_is_set(
    client: TestClient, fleek: FakeFleek, monkeypatch: pytest.MonkeyPatch
) -> None:
    class S:
        turnstile_secret_key = "secret"
        app_env = "test"

    monkeypatch.setattr(captcha, "get_settings", lambda: S())
    assert _start(client).status_code == 400
    assert fleek.whatsapp == []


def test_captcha_refuses_in_production_without_a_secret(
    client: TestClient, fleek: FakeFleek, monkeypatch: pytest.MonkeyPatch
) -> None:
    class S:
        turnstile_secret_key = None
        app_env = "production"

    monkeypatch.setattr(captcha, "get_settings", lambda: S())
    assert _start(client).status_code == 503


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
    bad = {"email": SIGNUP["email"], "password": "wrong-pass-1"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    assert client.post("/api/auth/login", json=bad).status_code == 429
    good = {"email": SIGNUP["email"], "password": SIGNUP["password"]}
    assert client.post("/api/auth/login", json=good).status_code == 429  # locked, even for the right one


def test_a_good_login_forgets_earlier_slips(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    for _ in range(3):
        client.post("/api/auth/login", json={"email": SIGNUP["email"], "password": "nope-nope-1"})
    good = {"email": SIGNUP["email"], "password": SIGNUP["password"]}
    assert client.post("/api/auth/login", json=good).status_code == 200
    for _ in range(4):  # the three slips no longer count against the five
        client.post("/api/auth/login", json={"email": SIGNUP["email"], "password": "nope-nope-1"})
    assert client.post("/api/auth/login", json=good).status_code == 200


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
    login = {"email": SIGNUP["email"], "password": "n3w-secret-pw"}
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


def test_forgot_password_is_silent_for_unknown_emails_and_resets_for_real_ones(
    client: TestClient, fleek: FakeFleek
) -> None:
    old = _signup(client, fleek)
    n_email = len(fleek.email)
    r = client.post("/api/auth/password/forgot", json={"email": "nobody@nowhere.example.com"})
    assert r.status_code == 204 and len(fleek.email) == n_email

    r = client.post("/api/auth/password/forgot", json={"email": SIGNUP["email"]})
    assert r.status_code == 204 and fleek.email[-1][2] == "reset"
    code = fleek.last_code("email", "reset")

    bad = client.post(
        "/api/auth/password/reset",
        json={"email": SIGNUP["email"], "code": "000000", "new_password": "n3w-secret-pw"},
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/password/reset",
        json={"email": SIGNUP["email"], "code": code, "new_password": "n3w-secret-pw"},
    )
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=_h(old)).status_code == 401
    # the code is single use
    again = client.post(
        "/api/auth/password/reset",
        json={"email": SIGNUP["email"], "code": code, "new_password": "an0ther-secret"},
    )
    assert again.status_code == 400


def test_reset_by_whatsapp_goes_only_to_a_verified_mobile(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    n = len(fleek.whatsapp)
    client.post("/api/auth/password/forgot", json={"email": SIGNUP["email"], "channel": "whatsapp"})
    assert len(fleek.whatsapp) == n + 1


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
    assert ov["done"] == 0 and ov["total"] == 6 and ov["plan_tier"] == "free"
    assert {c["key"]: c["status"] for c in ov["features"]}["whatsapp"] == "locked"

    client.patch("/api/tenant", headers=_h(t), json={"address": "12 Main Road", "state_code": "27"})
    client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"})
    ov = client.get("/api/onboarding", headers=_h(t)).json()
    done = {s["key"]: s["done"] for s in ov["steps"]}
    assert done["firm"] and done["party"] and not done["item"] and not done["invoice"]


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

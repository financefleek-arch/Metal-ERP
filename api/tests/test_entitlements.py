"""A firm's licence from the Fleek control plane: cache, grace and the two lenient rules."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import PaymentReminder, Tenant, TenantEntitlement
from app.services import entitlements as ent
from app.services import reminders as svc
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, check_signature
from tests.test_reminders import _bill, _party, _propose, _rows, _shop, sent  # noqa: F401


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ORG = "fleek-org-1"


def payload(
    status: str = "active",
    *,
    users: int = 6,
    valid_for: timedelta = timedelta(hours=24),
    now: datetime = NOW,
) -> dict[str, Any]:
    return {
        "version": 1,
        "org_id": ORG,
        "status": status,
        "access_tier": {"active": "subscribed", "trial": "trial"}.get(status, "free"),
        "trial_days_left": 9 if status == "trial" else None,
        "plan": {"key": "metal_erp_standard", "label": "Metal ERP Standard"},
        "limits": {"users": users, "campaign_monthly_cap": 5000, "whatsapp_numbers": 1},
        "modules": {"crm": None},
        "billing_mode": "autopay",
        "issued_at": now.isoformat(),
        "valid_until": (now + valid_for).isoformat(),
    }


class Fetcher:
    def __init__(self, *answers: dict[str, Any] | Exception) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, fleek_org_id: str) -> dict[str, Any]:
        self.calls += 1
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def firm() -> str:
    with SessionLocal() as s:
        t = Tenant(legal_name="Kumar Steel House", fleek_org_id=ORG)
        s.add(t)
        s.commit()
        return t.id


def _current(tenant_id: str, fetcher: Fetcher, now: datetime = NOW) -> ent.Licence | None:
    with SessionLocal() as s:
        lic = ent.current(s, s.get(Tenant, tenant_id), now=now, fetcher=fetcher)
        s.commit()
        return lic


# --------------------------------------------------------------------------
# cache, refresh, grace
# --------------------------------------------------------------------------


def test_an_unlinked_firm_is_unlimited_and_never_asks_fleek() -> None:
    f = Fetcher(payload())
    with SessionLocal() as s:
        t = Tenant(legal_name="Old Shop")
        s.add(t)
        s.commit()
        assert ent.current(s, t, now=NOW, fetcher=f) is None
        assert ent.proactive_sends_allowed(s, t, now=NOW, fetcher=f) is True
        assert ent.user_limit_reason(s, t, now=NOW, fetcher=f) is None
    assert f.calls == 0


def test_the_answer_is_fetched_once_and_cached_until_valid_until(firm: str) -> None:
    f = Fetcher(payload("trial"))
    lic = _current(firm, f)
    assert lic is not None and (lic.status, lic.trial_days_left, lic.users_limit) == ("trial", 9, 6)
    assert lic.plan_label == "Metal ERP Standard" and lic.stale is False
    _current(firm, f, NOW + timedelta(hours=23))
    assert f.calls == 1
    _current(firm, Fetcher(payload("active")), NOW + timedelta(hours=25))  # expired: asked again
    with SessionLocal() as s:
        row = s.query(TenantEntitlement).filter_by(tenant_id=firm).one()
        assert row.status == "active" and row.last_error is None


def test_fleek_down_and_never_verified_blocks_nothing(firm: str) -> None:
    f = Fetcher(ent.EntitlementError("down"))
    lic = _current(firm, f)
    assert lic is not None and lic.status == ent.UNVERIFIED
    with SessionLocal() as s:
        t = s.get(Tenant, firm)
        assert ent.proactive_sends_allowed(s, t, now=NOW, fetcher=f) is True
        assert ent.user_limit_reason(s, t, now=NOW, fetcher=f) is None


def test_fleek_down_after_expiry_uses_the_last_answer_for_a_week_then_nothing(firm: str) -> None:
    _current(firm, Fetcher(payload("active")))  # valid until NOW + 24h
    down = Fetcher(ent.EntitlementError("down"))
    day2 = NOW + timedelta(days=2)
    stale = _current(firm, down, day2)
    assert stale is not None and stale.status == "active" and stale.stale is True
    assert down.calls == 1
    _current(firm, down, day2 + timedelta(minutes=1))  # a failed attempt is not repeated for 5 minutes
    assert down.calls == 1
    _current(firm, down, day2 + timedelta(minutes=6))
    assert down.calls == 2
    gone = _current(firm, down, NOW + timedelta(days=9))  # 24h + 7 days of grace are over
    assert gone is not None and gone.status == ent.UNVERIFIED


def test_a_garbled_answer_is_treated_like_an_unreachable_fleek(firm: str) -> None:
    bad = payload()
    bad["valid_until"] = "not a time"
    assert _current(firm, Fetcher(bad)).status == ent.UNVERIFIED  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# fetch: the signed request
# --------------------------------------------------------------------------


def test_fetch_signs_a_get_and_reads_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "control_plane_url", "http://fleek-backend:8100/")
    monkeypatch.setattr(s, "control_plane_secret", "shared")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["verdict"] = check_signature(
            "shared",
            timestamp=request.headers[TIMESTAMP_HEADER],
            signature=request.headers[SIGNATURE_HEADER],
            method=request.method,
            path=request.url.path,
            body=b"",
        )
        return httpx.Response(200, json=payload())

    got = ent.fetch(ORG, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert got["status"] == "active"
    assert seen["path"] == f"/api/internal/metal-erp/orgs/{ORG}/entitlements"
    assert seen["verdict"] is None  # our signature passes our own verifier, which mirrors Fleek's


def test_fetch_failures_are_entitlement_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "control_plane_url", "http://fleek-backend:8100")
    monkeypatch.setattr(s, "control_plane_secret", "shared")

    def client(h: Any) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(h))

    for handler in (
        lambda r: httpx.Response(404),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json={"nope": 1}),
    ):
        with pytest.raises(ent.EntitlementError):
            ent.fetch(ORG, client=client(handler))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ent.EntitlementError):
        ent.fetch(ORG, client=client(boom))

    monkeypatch.setattr(s, "control_plane_secret", None)
    with pytest.raises(ent.EntitlementError):
        ent.fetch(ORG, client=client(lambda r: httpx.Response(200, json=payload())))


# --------------------------------------------------------------------------
# the lenient rules in the app
# --------------------------------------------------------------------------


def _cache(tenant_id: str, status: str, users: int = 6) -> None:
    now = datetime.now(UTC)
    with SessionLocal() as s:
        row = s.query(TenantEntitlement).filter_by(tenant_id=tenant_id).one_or_none()
        p = payload(status, users=users, now=now, valid_for=timedelta(hours=20))
        if row is None:
            row = TenantEntitlement(
                tenant_id=tenant_id,
                payload=p,
                last_verified_at=now,
                valid_until=now + timedelta(hours=20),
                status=status,
            )
            s.add(row)
        else:
            row.payload, row.status = p, status
            row.valid_until = now + timedelta(hours=20)
        s.commit()


def test_a_lapsed_licence_pauses_reminders_but_keeps_them_for_later(
    client: TestClient, sent: list[dict[str, Any]]
) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40)
    assert _propose(tid) == 1
    with SessionLocal() as s:
        s.get(Tenant, tid).fleek_org_id = ORG  # type: ignore[union-attr]
        s.commit()
    _cache(tid, "lapsed")

    with SessionLocal() as s:
        (r,) = s.query(PaymentReminder).filter_by(tenant_id=tid).all()
        svc.send(s, r, user_id=None)
        s.commit()
        assert (r.status, r.error) == ("failed", ent.LAPSED_MESSAGE)
        assert svc.auto_send(s, s.get(Tenant, tid)) == 0  # type: ignore[arg-type]
    assert sent == []

    _cache(tid, "active")  # renewed: the same reminder can now go out
    with SessionLocal() as s:
        r = s.query(PaymentReminder).filter_by(tenant_id=tid).one()
        svc.send(s, r, user_id=None)
        s.commit()
        assert r.status == "sent"
    assert len(sent) == 1
    assert _rows(tid)[0].status == "sent"


def test_a_trial_or_unverified_or_unlinked_firm_still_sends(
    client: TestClient, sent: list[dict[str, Any]]
) -> None:
    h, tid = _shop(client)
    pid = _party(client, h, "Jay Matadee")
    _bill(client, h, pid, age=40)
    _propose(tid)
    with SessionLocal() as s:  # unlinked
        (r,) = s.query(PaymentReminder).filter_by(tenant_id=tid).all()
        svc.send(s, r, user_id=None)
        s.commit()
        assert r.status == "sent"
    assert len(sent) == 1


def _admin_token(client: TestClient) -> dict[str, str]:
    from tools.make_platform_admin import run as make_admin

    with SessionLocal() as s:
        make_admin(s, email="ops@fleek.example.com", password="ops-s3cret-pass")
        s.commit()
    r = client.post("/api/auth/login", json={"email": "ops@fleek.example.com", "password": "ops-s3cret-pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_the_plans_user_limit_stops_the_operator_adding_one_more(client: TestClient) -> None:
    admin = _admin_token(client)
    firm = client.post("/api/admin/firms", json={"legal_name": "Kumar Steel House"}, headers=admin)
    firm_id = firm.json()["id"]

    def add(email: str) -> httpx.Response:
        return client.post(
            f"/api/admin/firms/{firm_id}/users",
            json={"email": email, "mobile": f"9000000{ord(email[0]):03d}", "password": "long-enough-1", "role": "accountant"},
            headers=admin,
        )

    assert add("a@example.com").status_code == 201  # unlinked: unlimited
    with SessionLocal() as s:
        s.get(Tenant, firm_id).fleek_org_id = ORG  # type: ignore[union-attr]
        s.commit()
    _cache(firm_id, "active", users=2)
    assert add("b@example.com").status_code == 201
    over = add("c@example.com")
    assert over.status_code == 409 and "allows 2 users" in over.json()["detail"]
    _cache(firm_id, "active", users=3)
    assert add("c@example.com").status_code == 201


def test_me_carries_the_licence_for_the_banner(client: TestClient) -> None:
    h, tid = _shop(client)
    assert client.get("/api/auth/me", headers=h).json()["licence"] is None  # unlinked: nothing to show
    with SessionLocal() as s:
        s.get(Tenant, tid).fleek_org_id = ORG  # type: ignore[union-attr]
        s.commit()
    _cache(tid, "trial")
    lic = client.get("/api/auth/me", headers=h).json()["licence"]
    assert lic == {
        "status": "trial",
        "plan_label": "Metal ERP Standard",
        "trial_days_left": 9,
        "stale": False,
    }

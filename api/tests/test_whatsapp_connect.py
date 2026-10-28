"""The owner's WhatsApp page mirrors Fleek's advisor page; Fleek's answers pass through."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Tenant, TenantWhatsappConfig, User
from app.security import hash_password
from app.services import whatsapp_connect as svc
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, check_signature

SECRET = "test-control-plane-secret"
ORG = "org-shop-1"
BASE = f"/api/internal/metal-erp/orgs/{ORG}/whatsapp-integrations"
POPUP = {"code": "abc", "waba_id": "w1", "phone_number_id": "pn1", "business_name": "Sethia"}
VIEW = {
    "phone_number_id": "pn1",
    "waba_id": "w1",
    "display_phone_number": "+91 90000 11111",
    "business_name": "Sethia",
    "mode": "coexistence",
    "registered": True,
    "connected_at": "2026-10-21T00:00:00+00:00",
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cp(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "control_plane_secret", SECRET)
    monkeypatch.setattr(s, "control_plane_url", "https://fleek.test")


class FakeFleek:
    """Answers like Fleek's signed routes: Fleek's own status body, plus `connection`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: list[httpx.Request] = []
        self.connected = False
        self.needs_registration = False
        self.refuse: tuple[int, dict[str, Any]] | None = None
        real = svc.httpx.Client

        def state() -> dict[str, Any]:
            live = self.connected
            return {
                "connected": live,
                "needs_reconnect": False,
                "needs_registration": live and self.needs_registration,
                "connection_mode": "coexistence" if live else None,
                "display_phone_number": VIEW["display_phone_number"] if live else None,
                "business_name": "Sethia" if live else None,
                "profile": None,
                "connection": VIEW if live else None,
                "templates": {"approved": 4, "pending": 2, "rejected": 0, "total": 6} if live else None,
            }

        def handler(request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            path = request.url.path.removeprefix(BASE)
            if self.refuse and request.method == "POST":
                return httpx.Response(self.refuse[0], json=self.refuse[1])
            if request.method == "POST" and path == "/connect":
                self.connected = True
                return httpx.Response(200, json={"status": "success", **state()})
            if request.method == "POST" and path == "/disconnect":
                self.connected = False
                return httpx.Response(200, json={"status": "success"})
            if request.method == "POST" and path == "/retry-registration":
                self.needs_registration = False
                return httpx.Response(200, json={"status": "success"})
            return httpx.Response(200, json=state())

        monkeypatch.setattr(
            svc.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
        )


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _shop(client: TestClient, *, linked: bool = True) -> tuple[dict[str, str], str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": "owner@x.example.com", "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    owner = _h(r.json()["access_token"])
    me = client.get("/api/auth/me", headers=owner).json()
    if linked:
        with SessionLocal() as s:
            s.get(Tenant, me["tenant_id"]).fleek_org_id = ORG  # type: ignore[union-attr]
            s.commit()
    return owner, me["tenant_id"]


def _colleague(client: TestClient, tenant_id: str, role: str) -> dict[str, str]:
    with SessionLocal() as s:
        s.add(
            User(
                tenant_id=tenant_id,
                email=f"{role}@x.example.com",
                password_hash=hash_password("s3cret-pass"),
                role=role,
            )
        )
        s.commit()
    r = client.post("/api/auth/login", json={"email": f"{role}@x.example.com", "password": "s3cret-pass"})
    return _h(r.json()["access_token"])


def _cfg(tenant_id: str) -> TenantWhatsappConfig | None:
    with SessionLocal() as s:
        return s.scalar(select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == tenant_id))


def test_connect_goes_to_fleek_signed_with_the_popup_body_and_the_number_is_cached(
    client, monkeypatch
) -> None:
    fleek = FakeFleek(monkeypatch)
    owner, tenant_id = _shop(client)
    r = client.post("/api/whatsapp-integrations/connect", json=POPUP, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success" and r.json()["connected"] is True
    assert "connection" not in r.json()  # the ids are for this app's server, not the page
    # the plan gate may ask Fleek for the licence first (cached after that); the connect call is the one that matters here
    req = next(r for r in fleek.seen if r.url.path.endswith("/connect"))
    assert req.url.path == f"{BASE}/connect" and json.loads(req.content) == POPUP
    check_signature(
        SECRET,
        timestamp=req.headers[TIMESTAMP_HEADER],
        signature=req.headers[SIGNATURE_HEADER],
        method="POST",
        path=req.url.path,
        body=req.content,
    )
    cfg = _cfg(tenant_id)
    assert cfg is not None
    assert (cfg.phone_number_id, cfg.waba_id, cfg.is_active) == ("pn1", "w1", True)
    assert cfg.display_phone_number == "+91 90000 11111"


def test_status_is_fleeks_status_and_refreshes_the_cache(client, monkeypatch) -> None:
    fleek = FakeFleek(monkeypatch)
    owner, tenant_id = _shop(client)
    assert client.get("/api/whatsapp-integrations", headers=owner).json()["connected"] is False
    assert _cfg(tenant_id) is None
    fleek.connected = True  # connected on Fleek by other means (a live shop adopted into the control plane)
    got = client.get("/api/whatsapp-integrations", headers=owner).json()
    assert got["connected"] is True and got["connection_mode"] == "coexistence"
    cfg = _cfg(tenant_id)
    assert cfg is not None and cfg.is_active is True


def test_the_owner_sees_how_many_messages_whatsapp_has_approved(client, monkeypatch) -> None:
    fleek = FakeFleek(monkeypatch)
    owner, _ = _shop(client)
    assert client.get("/api/whatsapp-integrations", headers=owner).json()["templates"] is None
    fleek.connected = True
    out = client.get("/api/whatsapp-integrations", headers=owner).json()
    assert out["templates"] == {"approved": 4, "pending": 2, "rejected": 0, "total": 6}
    assert "connection" not in out  # the number's ids stay on the server


def test_a_number_pending_registration_is_not_sent_from_until_retry_works(client, monkeypatch) -> None:
    fleek = FakeFleek(monkeypatch)
    owner, tenant_id = _shop(client)
    fleek.needs_registration = True
    client.post(
        "/api/whatsapp-integrations/connect",
        json={**POPUP, "onboarding": "cloud", "pin": "123456"},
        headers=owner,
    )
    cfg = _cfg(tenant_id)
    assert cfg is not None and cfg.is_active is False
    r = client.post("/api/whatsapp-integrations/retry-registration", json={"pin": "654321"}, headers=owner)
    assert r.status_code == 200 and r.json() == {"status": "success"}
    cfg = _cfg(tenant_id)
    assert cfg is not None and cfg.is_active is True


def test_disconnect_turns_the_cached_number_off(client, monkeypatch) -> None:
    FakeFleek(monkeypatch)
    owner, tenant_id = _shop(client)
    client.post("/api/whatsapp-integrations/connect", json=POPUP, headers=owner)
    r = client.post("/api/whatsapp-integrations/disconnect", headers=owner)
    assert r.status_code == 200 and r.json() == {"status": "success"}
    cfg = _cfg(tenant_id)
    assert cfg is not None and cfg.is_active is False


def test_fleeks_refusals_pass_through_with_its_status_and_nothing_is_cached(client, monkeypatch) -> None:
    fleek = FakeFleek(monkeypatch)
    fleek.refuse = (502, {"error": "connect_failed", "message": "Meta rejected code exchange"})
    owner, tenant_id = _shop(client)
    r = client.post("/api/whatsapp-integrations/connect", json=POPUP, headers=owner)
    assert r.status_code == 502 and r.json()["error"] == "connect_failed"
    assert _cfg(tenant_id) is None


def test_only_the_owner_and_only_a_linked_firm(client, monkeypatch) -> None:
    FakeFleek(monkeypatch)
    owner, tenant_id = _shop(client)
    for role in ("accountant", "counter", "viewer"):
        who = _colleague(client, tenant_id, role)
        assert client.post("/api/whatsapp-integrations/connect", json=POPUP, headers=who).status_code == 403
        assert client.get("/api/whatsapp-integrations", headers=who).status_code == 403
        assert client.post("/api/whatsapp-integrations/disconnect", headers=who).status_code == 403
    assert client.post("/api/whatsapp-integrations/connect", json=POPUP).status_code == 401


def test_an_unlinked_firm_is_told_to_ask_fleek(client, monkeypatch) -> None:
    FakeFleek(monkeypatch)
    owner, _ = _shop(client, linked=False)
    r = client.get("/api/whatsapp-integrations", headers=owner)
    assert r.status_code == 409 and "linked" in r.json()["detail"]


def test_a_number_another_firm_already_sends_from_is_not_activated_here(client, monkeypatch) -> None:
    FakeFleek(monkeypatch)
    owner, tenant_id = _shop(client)
    with SessionLocal() as s:
        other = Tenant(legal_name="Other Firm")
        s.add(other)
        s.flush()
        s.add(TenantWhatsappConfig(tenant_id=other.id, phone_number_id="pn1", waba_id="w9", is_active=True))
        s.commit()
    client.post("/api/whatsapp-integrations/connect", json=POPUP, headers=owner)
    cfg = _cfg(tenant_id)
    assert cfg is not None and cfg.is_active is False


def test_fleek_unreachable_or_unconfigured_is_a_502(client, monkeypatch) -> None:
    FakeFleek(monkeypatch)
    owner, _ = _shop(client)
    monkeypatch.setattr(get_settings(), "control_plane_url", None)
    assert client.get("/api/whatsapp-integrations", headers=owner).status_code == 502


def test_fleek_refusing_this_servers_signature_does_not_sign_the_owner_out(client, monkeypatch) -> None:
    fleek = FakeFleek(monkeypatch)
    fleek.refuse = (401, {"detail": "Invalid signature"})
    owner, _ = _shop(client)
    r = client.post("/api/whatsapp-integrations/connect", json=POPUP, headers=owner)
    assert r.status_code == 502 and "not available" in r.json()["message"]

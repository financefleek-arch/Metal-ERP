"""One console: Fleek's operator actions on a shop reach Metal ERP's admin functions by signed routes."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Tenant, TenantEntitlement, TenantWhatsappConfig, User
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from tools.make_platform_admin import run as make_admin

SECRET = "test-control-plane-secret"
ORG = "fleek-org-77"
BASE = f"/api/internal/control-plane/firms/{ORG}"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "control_plane_secret", SECRET)


@pytest.fixture
def firm() -> str:
    with SessionLocal() as s:
        t = Tenant(legal_name="Kumar Steel House", city="Kolkata", fleek_org_id=ORG)
        s.add(t)
        s.commit()
        return t.id


def _req(
    client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    secret: str = SECRET,
) -> Any:
    body = json.dumps(payload).encode() if payload is not None else b""
    stamp = str(int(time.time()))
    headers = {
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: sign(secret, timestamp=stamp, method=method, path=path, body=body),
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.request(method, path, content=body, headers=headers)


def _licence(firm_id: str, users: int) -> None:
    now = datetime.now(UTC)
    payload = {
        "status": "active",
        "plan": {"key": "metal_erp_standard", "label": "Standard"},
        "limits": {"users": users, "campaign_monthly_cap": 5000, "whatsapp_numbers": 1},
        "valid_until": (now + timedelta(hours=20)).isoformat(),
    }
    with SessionLocal() as s:
        s.add(
            TenantEntitlement(
                tenant_id=firm_id,
                payload=payload,
                last_verified_at=now,
                valid_until=now + timedelta(hours=20),
                status="active",
            )
        )
        s.commit()


# --------------------------------------------------------------------------
# the door
# --------------------------------------------------------------------------


def test_every_route_is_signed_and_only_for_a_linked_firm(client: TestClient, firm: str) -> None:
    calls = [
        ("GET", ""),
        ("PATCH", ""),
        ("GET", "/users"),
        ("POST", "/users"),
        ("GET", "/tally-shop"),
        ("POST", "/tally-shop/rotate-key"),
        ("GET", "/tally-shop/installer"),
    ]
    for method, suffix in calls:
        assert client.request(method, BASE + suffix).status_code == 401, (method, suffix)
        assert _req(client, method, BASE + suffix, secret="wrong").status_code == 401
    other = "/api/internal/control-plane/firms/not-linked"
    assert _req(client, "GET", other).status_code == 404
    assert _req(client, "GET", other + "/users").status_code == 404
    assert _req(client, "GET", other + "/tally-shop").status_code == 404


# --------------------------------------------------------------------------
# the firm and its switches
# --------------------------------------------------------------------------


def test_the_firm_and_its_switches_are_read_and_changed(client: TestClient, firm: str) -> None:
    got = _req(client, "GET", BASE)
    assert got.status_code == 200 and got.json()["legal_name"] == "Kumar Steel House"
    assert got.json()["gst_enabled"] is False
    changed = _req(
        client,
        "PATCH",
        BASE,
        {"gst_enabled": True, "ext_inward_import": True, "reminder_auto_allowed": False, "city": "Howrah"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["gst_enabled"], body["ext_inward_import"], body["reminder_auto_allowed"], body["city"]) == (
        True,
        True,
        False,
        "Howrah",
    )
    with SessionLocal() as s:
        t = s.get(Tenant, firm)
        assert t is not None and t.gst_enabled and t.ext_inward_import and not t.reminder_auto_allowed
    assert (
        _req(client, "PATCH", BASE, {"legal_name": "x"}).status_code == 422
    )  # the same rules as the operator console


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


def test_users_are_added_changed_and_disabled_with_the_plans_limit(client: TestClient, firm: str) -> None:
    _licence(firm, users=2)
    a = _req(
        client,
        "POST",
        BASE + "/users",
        {"email": "a@example.com", "password": "long-enough-1", "role": "owner"},
    )
    assert a.status_code == 201, a.text
    b = _req(
        client,
        "POST",
        BASE + "/users",
        {"email": "b@example.com", "password": "long-enough-1", "role": "accountant"},
    )
    assert b.status_code == 201
    over = _req(
        client,
        "POST",
        BASE + "/users",
        {"email": "c@example.com", "password": "long-enough-1", "role": "viewer"},
    )
    assert over.status_code == 409 and "allows 2 users" in over.json()["detail"]
    dup = _req(
        client,
        "POST",
        BASE + "/users",
        {"email": "A@example.com", "password": "long-enough-1", "role": "viewer"},
    )
    assert dup.status_code == 409

    listed = _req(client, "GET", BASE + "/users").json()
    assert sorted(u["email"] for u in listed) == ["a@example.com", "b@example.com"]
    uid = b.json()["id"]
    patched = _req(client, "PATCH", f"{BASE}/users/{uid}", {"role": "counter"})
    assert patched.status_code == 200 and patched.json()["role"] == "counter"
    assert _req(client, "DELETE", f"{BASE}/users/{uid}").status_code == 204
    with SessionLocal() as s:
        assert s.get(User, uid).is_active is False  # type: ignore[union-attr]
    # the last active owner cannot be switched off (the operator console's own guard)
    owner = a.json()["id"]
    assert _req(client, "DELETE", f"{BASE}/users/{owner}").status_code == 409


def test_a_user_of_another_firm_is_not_reachable(client: TestClient, firm: str) -> None:
    with SessionLocal() as s:
        other = Tenant(legal_name="Someone Else", fleek_org_id="fleek-org-other")
        s.add(other)
        s.flush()
        u = User(tenant_id=other.id, email="x@example.com", password_hash="x", role="owner")
        s.add(u)
        s.commit()
        uid = u.id
    assert _req(client, "PATCH", f"{BASE}/users/{uid}", {"role": "viewer"}).status_code == 404
    assert _req(client, "DELETE", f"{BASE}/users/{uid}").status_code == 404
    with SessionLocal() as s:
        assert s.get(User, uid).is_active is True  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Tally agent
# --------------------------------------------------------------------------


def test_tally_agent_reads_and_refusals_without_an_agent(client: TestClient, firm: str) -> None:
    got = _req(client, "GET", BASE + "/tally-shop")
    assert got.status_code == 200 and got.json()["provisioned"] is False
    assert _req(client, "POST", BASE + "/tally-shop/rotate-key").status_code == 404
    assert _req(client, "PUT", BASE + "/tally-shop/target-version", {"version": None}).status_code == 404
    assert _req(client, "GET", BASE + "/tally-shop/installer").status_code == 404


# --------------------------------------------------------------------------
# the Metal ERP console no longer edits a linked firm's WhatsApp number
# --------------------------------------------------------------------------


def _operator(client: TestClient) -> dict[str, str]:
    with SessionLocal() as s:
        make_admin(s, email="ops@fleek.example.com", password="ops-s3cret-pass")
        s.commit()
    r = client.post("/api/auth/login", json={"email": "ops@fleek.example.com", "password": "ops-s3cret-pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_a_linked_firms_number_cannot_be_pasted_in_the_metal_erp_console(
    client: TestClient, firm: str
) -> None:
    h = _operator(client)
    body = {"phone_number_id": "pn-1", "waba_id": "waba-1", "is_active": True}
    r = client.put(f"/api/admin/firms/{firm}/whatsapp", json=body, headers=h)
    assert r.status_code == 409 and "managed from Fleek" in r.json()["detail"]
    assert client.delete(f"/api/admin/firms/{firm}/whatsapp", headers=h).status_code == 409
    with SessionLocal() as s:
        assert s.scalar(select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == firm)) is None


def test_an_unlinked_firm_still_takes_a_pasted_number(client: TestClient) -> None:
    h = _operator(client)
    firm_id = client.post("/api/admin/firms", json={"legal_name": "Old Shop"}, headers=h).json()["id"]
    body = {"phone_number_id": "pn-9", "waba_id": "waba-9", "is_active": True}
    assert client.put(f"/api/admin/firms/{firm_id}/whatsapp", json=body, headers=h).status_code == 200
    assert client.delete(f"/api/admin/firms/{firm_id}/whatsapp", headers=h).status_code == 204

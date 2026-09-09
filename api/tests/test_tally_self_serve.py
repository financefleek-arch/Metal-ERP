"""Tenant-scoped Tally agent surface (`/api/tally/*`) — the firm's own
login downloading its own installer, distinct from the platform-admin route
in `test_tally_agent.py` / `test_tally_connector.py`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.routers.admin as admin_router
import app.routers.tally_self_serve as self_serve_router
import app.services.tally.installer as installer_mod
from app.db import SessionLocal
from app.main import app
from app.models import Tenant
from tools.make_platform_admin import run as make_admin


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_installer_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_build(session, shop, *, plaintext_key):  # type: ignore[no-untyped-def]
        shop.installer_r2_key = f"installers/{shop.id}.zip"
        session.flush()
        return shop.installer_r2_key

    monkeypatch.setattr(installer_mod, "build_and_cache_installer", _fake_build)
    monkeypatch.setattr(admin_router, "get_object", lambda r2_key: b"PK\x03\x04fake-zip")
    monkeypatch.setattr(self_serve_router, "get_object", lambda r2_key: b"PK\x03\x04fake-zip")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_firm(client: TestClient, name: str, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": name, "email": email, "password": "pw123456"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["access_token"]


def _admin_token(client: TestClient) -> str:
    with SessionLocal() as s:
        make_admin(s, email="ops@self-serve.example.com", password="ops-s3cret-pass")
        s.commit()
    r = client.post(
        "/api/auth/login",
        json={"email": "ops@self-serve.example.com", "password": "ops-s3cret-pass"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_agent_status_before_and_after_provision(client: TestClient) -> None:
    tok = _register_firm(client, "Self Serve Traders", "owner@self-serve.example.com")

    r = client.get("/api/tally/agent-status", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["provisioned"] is False

    with SessionLocal() as s:
        firm_id = s.scalar(select(Tenant.id).where(Tenant.legal_name == "Self Serve Traders"))

    admin_tok = _admin_token(client)
    r = client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(admin_tok))
    assert r.status_code == 201, r.text

    r = client.get("/api/tally/agent-status", headers=_auth(tok))
    assert r.json()["provisioned"] is True
    assert r.json()["installer_ready"] is True


def test_installer_download_requires_provisioning(client: TestClient) -> None:
    tok = _register_firm(client, "No Agent Yet Co", "owner2@self-serve.example.com")
    r = client.get("/api/tally/installer", headers=_auth(tok))
    assert r.status_code == 404


def test_installer_download_is_tenant_scoped(client: TestClient) -> None:
    tok_a = _register_firm(client, "Firm A Traders", "owner-a@self-serve.example.com")
    tok_b = _register_firm(client, "Firm B Traders", "owner-b@self-serve.example.com")

    with SessionLocal() as s:
        firm_a = s.scalar(select(Tenant.id).where(Tenant.legal_name == "Firm A Traders"))

    admin_tok = _admin_token(client)
    r = client.post(f"/api/admin/firms/{firm_a}/tally-shop", headers=_auth(admin_tok))
    assert r.status_code == 201, r.text

    # firm A can download its own installer
    r = client.get("/api/tally/installer", headers=_auth(tok_a))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    # firm B has no agent yet — 404, never firm A's zip
    r = client.get("/api/tally/installer", headers=_auth(tok_b))
    assert r.status_code == 404

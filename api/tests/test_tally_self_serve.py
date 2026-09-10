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
import app.services.tally.backup_retention as retention_mod
import app.services.tally.installer as installer_mod
from app.db import SessionLocal
from app.main import app
from app.models import BackupShop, BackupUpload, Tenant
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


# --------------------------------------------------------------------------
# cloud backups — list, download, retention
# --------------------------------------------------------------------------


def _provision_shop(client: TestClient, firm_name: str) -> str:
    """Provision an agent for the named firm; return its shop_id."""
    with SessionLocal() as s:
        firm_id = s.scalar(select(Tenant.id).where(Tenant.legal_name == firm_name))
    admin_tok = _admin_token(client)
    r = client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(admin_tok))
    assert r.status_code == 201, r.text
    return r.json()["shop_id"]


def _add_upload(shop_id: str, name: str, *, status_: str = "confirmed", ts=None) -> str:
    from datetime import UTC, datetime

    with SessionLocal() as s:
        row = BackupUpload(
            shop_id=shop_id,
            filename=name,
            size_bytes=1234,
            r2_key=f"{shop_id}/{name}",
            status=status_,
            uploaded_at=ts or datetime.now(UTC),
        )
        s.add(row)
        s.commit()
        return row.id


def test_list_backups_only_own_confirmed(client: TestClient) -> None:
    tok_a = _register_firm(client, "Backup Firm A", "b-a@self-serve.example.com")
    _register_firm(client, "Backup Firm B", "b-b@self-serve.example.com")
    shop_a = _provision_shop(client, "Backup Firm A")
    shop_b = _provision_shop(client, "Backup Firm B")

    _add_upload(shop_a, "a1.001")
    _add_upload(shop_a, "a2.001", status_="pending")
    _add_upload(shop_b, "b1.001")

    r = client.get("/api/tally/backups", headers=_auth(tok_a))
    assert r.status_code == 200, r.text
    body = r.json()
    names = {b["filename"] for b in body["backups"]}
    assert names == {"a1.001"}  # own confirmed only
    assert body["retention_count"] == 30


def test_download_backup_tenant_scoped(client: TestClient, monkeypatch) -> None:
    tok_a = _register_firm(client, "DL Firm A", "dl-a@self-serve.example.com")
    tok_b = _register_firm(client, "DL Firm B", "dl-b@self-serve.example.com")
    shop_a = _provision_shop(client, "DL Firm A")
    _provision_shop(client, "DL Firm B")
    up_a = _add_upload(shop_a, "books.001")

    monkeypatch.setattr(self_serve_router, "get_object", lambda r2_key: b"TALLYBACKUPBYTES")

    r = client.get(f"/api/tally/backups/{up_a}/download", headers=_auth(tok_a))
    assert r.status_code == 200
    assert r.content == b"TALLYBACKUPBYTES"
    assert "books.001" in r.headers["content-disposition"]

    # firm B cannot fetch firm A's backup id
    r = client.get(f"/api/tally/backups/{up_a}/download", headers=_auth(tok_b))
    assert r.status_code == 404


def test_retention_prunes_oldest_confirmed(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from app.config import get_settings

    deleted: list[str] = []
    monkeypatch.setattr(retention_mod, "delete_object", lambda key: deleted.append(key))

    keep = get_settings().tally_backup_retention_count
    with SessionLocal() as s:
        shop = BackupShop(name="Prune Co", api_key_hash="h-prune")
        s.add(shop)
        s.flush()
        base = datetime.now(UTC)
        for i in range(keep + 3):
            s.add(
                BackupUpload(
                    shop_id=shop.id,
                    filename=f"b{i}.001",
                    size_bytes=1,
                    r2_key=f"{shop.id}/b{i}.001",
                    status="confirmed",
                    uploaded_at=base - timedelta(hours=i),
                )
            )
        # a failed row must never be pruned
        s.add(
            BackupUpload(
                shop_id=shop.id,
                filename="stale-failed.001",
                size_bytes=1,
                r2_key=f"{shop.id}/stale-failed.001",
                status="failed",
                uploaded_at=base - timedelta(days=9),
            )
        )
        s.commit()

        removed = retention_mod.prune_confirmed_backups(s, shop)
        s.commit()

        assert removed == 3
        assert len(deleted) == 3
        remaining = list(
            s.scalars(select(BackupUpload).where(BackupUpload.shop_id == shop.id))
        )
        assert len([r for r in remaining if r.status == "confirmed"]) == keep
        assert any(r.filename == "stale-failed.001" for r in remaining)


def test_retention_per_shop_override(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(retention_mod, "delete_object", lambda key: None)

    with SessionLocal() as s:
        shop = BackupShop(name="Override Co", api_key_hash="h-override", backup_retention_count=2)
        s.add(shop)
        s.flush()
        base = datetime.now(UTC)
        for i in range(5):
            s.add(
                BackupUpload(
                    shop_id=shop.id,
                    filename=f"o{i}.001",
                    size_bytes=1,
                    r2_key=f"{shop.id}/o{i}.001",
                    status="confirmed",
                    uploaded_at=base - timedelta(hours=i),
                )
            )
        s.commit()
        removed = retention_mod.prune_confirmed_backups(s, shop)
        s.commit()
        assert removed == 3
        remaining = list(
            s.scalars(select(BackupUpload).where(BackupUpload.shop_id == shop.id))
        )
        assert len(remaining) == 2

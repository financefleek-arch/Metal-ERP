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


def _add_upload(
    shop_id: str,
    name: str,
    *,
    status_: str = "confirmed",
    ts=None,
    set_id: str | None = None,
) -> str:
    from datetime import UTC, datetime

    with SessionLocal() as s:
        row = BackupUpload(
            shop_id=shop_id,
            filename=name,
            size_bytes=1234,
            r2_key=f"{shop_id}/{name}",
            status=status_,
            set_id=set_id,
            uploaded_at=ts or datetime.now(UTC),
        )
        s.add(row)
        s.commit()
        return row.id


def test_list_backups_groups_into_sets_own_confirmed(client: TestClient) -> None:
    tok_a = _register_firm(client, "Backup Firm A", "b-a@self-serve.example.com")
    _register_firm(client, "Backup Firm B", "b-b@self-serve.example.com")
    shop_a = _provision_shop(client, "Backup Firm A")
    shop_b = _provision_shop(client, "Backup Firm B")

    # one 2-file set for A, plus a pending file (excluded), plus B's own
    _add_upload(shop_a, "TBK1800_100000.900", set_id="tbk:1800_100000_v0")
    _add_upload(shop_a, "TDBK1800_100000.001", set_id="tbk:1800_100000_v0")
    _add_upload(shop_a, "half.001", status_="pending")
    _add_upload(shop_b, "TDBK1800_200000.001", set_id="tbk:1800_200000_v0")

    r = client.get("/api/tally/backups", headers=_auth(tok_a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retention_count"] == 30
    assert len(body["sets"]) == 1  # A's single confirmed set only
    the_set = body["sets"][0]
    assert the_set["set_id"] == "tbk:1800_100000_v0"
    assert {f["filename"] for f in the_set["files"]} == {
        "TBK1800_100000.900",
        "TDBK1800_100000.001",
    }
    assert the_set["total_bytes"] == 2468


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


def _add_set(s, shop_id: str, set_id: str, base_ts, *, parts: int = 3) -> None:
    """A backup set = one .900 manifest + `parts` TDBK data files, all
    sharing `set_id` and `base_ts`."""
    s.add(
        BackupUpload(
            shop_id=shop_id,
            filename=f"{set_id}.900",
            size_bytes=1,
            r2_key=f"{shop_id}/{set_id}.900",
            status="confirmed",
            set_id=set_id,
            uploaded_at=base_ts,
        )
    )
    for p in range(1, parts + 1):
        s.add(
            BackupUpload(
                shop_id=shop_id,
                filename=f"{set_id}.00{p}",
                size_bytes=1,
                r2_key=f"{shop_id}/{set_id}.00{p}",
                status="confirmed",
                set_id=set_id,
                uploaded_at=base_ts,
            )
        )


def test_retention_prunes_whole_sets_oldest_first(monkeypatch) -> None:
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
        # keep + 3 sets of 4 files each (1 manifest + 3 parts)
        for i in range(keep + 3):
            _add_set(s, shop.id, f"set-{i:03d}", base - timedelta(hours=i), parts=3)
        # a failed straggler must never be pruned
        s.add(
            BackupUpload(
                shop_id=shop.id,
                filename="stale-failed.001",
                size_bytes=1,
                r2_key=f"{shop.id}/stale-failed.001",
                status="failed",
                set_id="set-999",
                uploaded_at=base - timedelta(days=9),
            )
        )
        s.commit()

        removed = retention_mod.prune_confirmed_backups(s, shop)
        s.commit()

        # 3 whole sets * 4 files
        assert removed == 12
        assert len(deleted) == 12
        remaining = list(
            s.scalars(select(BackupUpload).where(BackupUpload.shop_id == shop.id))
        )
        confirmed = [r for r in remaining if r.status == "confirmed"]
        # exactly `keep` sets survive, each still whole (4 files)
        surviving_sets = {r.set_id for r in confirmed}
        assert len(surviving_sets) == keep
        for sid in surviving_sets:
            assert len([r for r in confirmed if r.set_id == sid]) == 4
        assert any(r.filename == "stale-failed.001" for r in remaining)


def test_retention_legacy_null_set_id_is_its_own_set(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(retention_mod, "delete_object", lambda key: None)

    with SessionLocal() as s:
        shop = BackupShop(name="Legacy Co", api_key_hash="h-legacy", backup_retention_count=2)
        s.add(shop)
        s.flush()
        base = datetime.now(UTC)
        # 5 pre-0030 single-file backups, no set_id -> 5 singleton sets
        for i in range(5):
            s.add(
                BackupUpload(
                    shop_id=shop.id,
                    filename=f"legacy{i}.001",
                    size_bytes=1,
                    r2_key=f"{shop.id}/legacy{i}.001",
                    status="confirmed",
                    uploaded_at=base - timedelta(hours=i),
                )
            )
        s.commit()
        removed = retention_mod.prune_confirmed_backups(s, shop)
        s.commit()
        assert removed == 3  # keep 2 newest singletons
        remaining = list(
            s.scalars(select(BackupUpload).where(BackupUpload.shop_id == shop.id))
        )
        assert len(remaining) == 2

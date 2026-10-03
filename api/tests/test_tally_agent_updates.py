"""Tally agent versioning + auto-update: checkin bookkeeping, update offers,
the ops pin endpoint, and the installer sourcing its build from the R2 release.

No real R2: `agent_release.get_object`/`presigned_get_url` are monkeypatched.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.routers.tally_agent as tally_agent_router
import app.services.tally.agent_release as rel_mod
import app.services.tally_agent_installer as installer
from app.db import SessionLocal
from app.main import app
from app.models import BackupShop, Tenant
from tools.make_backup_shop import run as make_shop
from tools.make_platform_admin import run as make_admin


def _release(version: str) -> rel_mod.Release:
    return rel_mod.Release(
        version=version,
        sha256="ab" * 32,
        size_bytes=1234,
        signature="c2ln",
        zip_key=f"agent-releases/{version}/tally-agent-{version}.zip",
    )


@pytest.fixture(autouse=True)
def _release_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, rel_mod.Release | None]:
    """Fake store: {"latest": Release|None, "<ver>": Release}."""
    store: dict[str, rel_mod.Release | None] = {"latest": None}
    rel_mod.clear_cache()
    monkeypatch.setattr(rel_mod, "get_latest_release", lambda: store.get("latest"))
    monkeypatch.setattr(rel_mod, "get_release", lambda v: store.get(v))
    monkeypatch.setattr(
        rel_mod.backup_storage, "presigned_get_url", lambda key, *a, **k: f"https://r2.test/{key}?sig=x"
    )
    monkeypatch.setattr(
        tally_agent_router,
        "presigned_put_url",
        lambda r2_key: (f"https://r2.example.com/{r2_key}?sig=stub", 900),
    )
    return store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _shop(session: Session) -> tuple[str, str]:
    shop_id, key, _ = make_shop(session, name="Upd Shop", tenant_id=None, rotate_key=False)
    session.commit()
    assert key
    return shop_id, key


def _checkin(client: TestClient, key: str, **body: object) -> dict:
    r = client.post("/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
# checkin bookkeeping
# --------------------------------------------------------------------------


def test_checkin_records_version_and_os(client: TestClient, session: Session) -> None:
    shop_id, key = _shop(session)
    _checkin(client, key, agent_version="1.0.0", os_version="Windows 11 / X64")
    session.expire_all()
    shop = session.get(BackupShop, shop_id)
    assert shop is not None
    assert shop.agent_version == "1.0.0"
    assert shop.os_version == "Windows 11 / X64"
    assert shop.agent_version_at is not None


def test_checkin_rejects_malformed_version(client: TestClient, session: Session) -> None:
    _shop_id, key = _shop(session)
    r = client.post(
        "/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={"agent_version": "v1"}
    )
    assert r.status_code == 422


def test_old_agent_without_version_gets_no_offer(
    client: TestClient, session: Session, _release_store: dict
) -> None:
    _release_store["latest"] = _release("2.0.0")
    _shop_id, key = _shop(session)
    assert _checkin(client, key)["update"] is None


# --------------------------------------------------------------------------
# offers
# --------------------------------------------------------------------------


def test_offer_when_latest_is_newer(
    client: TestClient, session: Session, _release_store: dict
) -> None:
    _release_store["latest"] = _release("1.1.0")
    _shop_id, key = _shop(session)
    out = _checkin(client, key, agent_version="1.0.0")["update"]
    assert out["version"] == "1.1.0"
    assert out["sha256"] == "ab" * 32
    assert out["size_bytes"] == 1234
    assert out["signature"] == "c2ln"
    assert out["url"].startswith("https://r2.test/agent-releases/1.1.0/")


def test_no_offer_when_current_or_ahead(
    client: TestClient, session: Session, _release_store: dict
) -> None:
    _release_store["latest"] = _release("1.1.0")
    _shop_id, key = _shop(session)
    assert _checkin(client, key, agent_version="1.1.0")["update"] is None
    assert _checkin(client, key, agent_version="1.2.0")["update"] is None  # never auto-downgrade


def test_pin_overrides_latest_and_allows_downgrade(
    client: TestClient, session: Session, _release_store: dict
) -> None:
    _release_store["latest"] = _release("1.2.0")
    _release_store["1.0.5"] = _release("1.0.5")
    shop_id, key = _shop(session)
    shop = session.get(BackupShop, shop_id)
    assert shop is not None
    shop.target_agent_version = "1.0.5"
    session.commit()
    out = _checkin(client, key, agent_version="1.1.0")["update"]
    assert out is not None and out["version"] == "1.0.5"
    assert _checkin(client, key, agent_version="1.0.5")["update"] is None


def test_failed_update_is_not_reoffered_until_retarget(
    client: TestClient, session: Session, _release_store: dict
) -> None:
    _release_store["latest"] = _release("1.1.0")
    shop_id, key = _shop(session)
    out = _checkin(
        client,
        key,
        agent_version="1.0.0",
        update_failed_version="1.1.0",
        update_error="unhealthy after 3 starts",
    )
    assert out["update"] is None  # same checkin that reports the failure
    session.expire_all()
    shop = session.get(BackupShop, shop_id)
    assert shop is not None
    assert shop.last_update_status == "failed:1.1.0"
    assert "unhealthy" in (shop.last_error or "")
    assert _checkin(client, key, agent_version="1.0.0")["update"] is None

    # a newer promoted release is offered again
    _release_store["latest"] = _release("1.1.1")
    out2 = _checkin(client, key, agent_version="1.0.0")["update"]
    assert out2 is not None and out2["version"] == "1.1.1"


def test_successful_move_clears_failed_marker(
    client: TestClient, session: Session, _release_store: dict
) -> None:
    shop_id, key = _shop(session)
    _checkin(client, key, agent_version="1.0.0", update_failed_version="1.1.0")
    _checkin(client, key, agent_version="1.1.1")
    session.expire_all()
    shop = session.get(BackupShop, shop_id)
    assert shop is not None
    assert shop.last_update_status is None


def test_release_store_failure_never_breaks_checkin(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("r2 down")

    monkeypatch.setattr(rel_mod, "get_latest_release", boom)
    _shop_id, key = _shop(session)
    assert _checkin(client, key, agent_version="1.0.0")["update"] is None


# --------------------------------------------------------------------------
# ops pin endpoint
# --------------------------------------------------------------------------


def _admin(client: TestClient) -> dict[str, str]:
    with SessionLocal() as s:
        make_admin(s, email="ops@upd.example.com", password="ops-s3cret-pass")
        s.commit()
    r = client.post(
        "/api/auth/login", json={"email": "ops@upd.example.com", "password": "ops-s3cret-pass"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_pin_endpoint_sets_clears_and_validates(
    client: TestClient, session: Session, _release_store: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.tally.installer as installer_mod

    def _fake_build(sess, shop, *, plaintext_key):  # type: ignore[no-untyped-def]
        shop.installer_r2_key = f"installers/{shop.id}.zip"
        shop.installer_agent_version = "1.0.0"
        sess.flush()
        return shop.installer_r2_key

    monkeypatch.setattr(installer_mod, "build_and_cache_installer", _fake_build)

    reg = client.post(
        "/api/auth/register",
        json={"firm_name": "Pin Firm", "email": "o@pin.example.com", "password": "pw123456"},
    )
    assert reg.status_code in (200, 201), reg.text
    firm_id = session.scalar(select(Tenant.id).where(Tenant.legal_name == "Pin Firm"))
    h = _admin(client)
    assert client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=h).status_code == 201

    _release_store["1.3.0"] = _release("1.3.0")
    _release_store["latest"] = _release("1.2.0")

    r = client.put(
        f"/api/admin/firms/{firm_id}/tally-shop/target-version",
        headers=h,
        json={"version": "1.3.0"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_agent_version"] == "1.3.0"
    assert body["latest_agent_version"] == "1.2.0"
    assert body["installer_agent_version"] == "1.0.0"

    r = client.put(
        f"/api/admin/firms/{firm_id}/tally-shop/target-version",
        headers=h,
        json={"version": "9.9.9"},
    )
    assert r.status_code == 422  # no such release — typo can't strand a shop

    r = client.put(
        f"/api/admin/firms/{firm_id}/tally-shop/target-version", headers=h, json={"version": None}
    )
    assert r.status_code == 200 and r.json()["target_agent_version"] is None

    assert (
        client.put(
            f"/api/admin/firms/{firm_id}/tally-shop/target-version", json={"version": None}
        ).status_code
        == 401
    )


# --------------------------------------------------------------------------
# manifest parsing + installer sourcing
# --------------------------------------------------------------------------


def test_parse_manifest_rejects_bad_input() -> None:
    good = {
        "version": "1.2.3",
        "sha256": "AB" * 32,
        "size_bytes": 10,
        "signature": "x",
        "zip_key": "agent-releases/1.2.3/a.zip",
    }
    parsed = rel_mod._parse(json.dumps(good).encode())
    assert parsed is not None and parsed.sha256 == "ab" * 32
    assert rel_mod._parse(b"not json") is None
    assert rel_mod._parse(json.dumps({**good, "version": "1.2"}).encode()) is None
    # a manifest must not be able to point the agent at an arbitrary key
    foreign = {**good, "zip_key": "installers/other.zip"}
    assert rel_mod._parse(json.dumps(foreign).encode()) is None
    no_sha = {k: v for k, v in good.items() if k != "sha256"}
    assert rel_mod._parse(json.dumps(no_sha).encode()) is None


def test_installer_uses_promoted_release_zip(
    monkeypatch: pytest.MonkeyPatch, _release_store: dict
) -> None:
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("publish/TallyAgent.exe", b"exe")
        z.writestr("publish/appsettings.json", '{"Agent": {"ShopApiKey": "TEMPLATE"}}')
        z.writestr("install.ps1", "# i")
        z.writestr("uninstall.ps1", "# u")
    rel = _release("1.4.0")
    _release_store["latest"] = rel
    monkeypatch.setattr(rel_mod, "release_zip_bytes", lambda r: src.getvalue())

    built = installer.build_installer(
        shop_api_key="sk_live_x", backend_base_url="https://api.example.com"
    )
    assert built.agent_version == "1.4.0"
    with zipfile.ZipFile(io.BytesIO(built.zip_bytes)) as zf:
        names = zf.namelist()
        assert "install.ps1" in names and "uninstall.ps1" in names
        assert "publish/TallyAgent.exe" in names
        assert names.count("publish/appsettings.json") == 1  # template dropped, per-shop kept
        assert json.loads(zf.read("publish/appsettings.json"))["Agent"]["ShopApiKey"] == "sk_live_x"


def test_installer_falls_back_to_build_dir_without_release(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = tmp_path / "b"
    build.mkdir()
    (build / "TallyAgent.exe").write_bytes(b"x")
    monkeypatch.setattr(installer._settings, "tally_agent_build_dir", str(build))
    built = installer.build_installer(shop_api_key="k", backend_base_url="https://a.example.com")
    assert built.agent_version is None
    with zipfile.ZipFile(io.BytesIO(built.zip_bytes)) as zf:
        assert "install.ps1" in zf.namelist()
        assert "uninstall.ps1" in zf.namelist()  # ships once the script exists in tally-agent/

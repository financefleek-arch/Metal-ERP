"""Tally companion agent API: shop checkin, backup upload, admin status.

Shops are staff-provisioned only (no self-serve signup), so tests create
them directly via `tools.make_backup_shop.run` against the `session`
fixture rather than through HTTP. `app.backup_storage.presigned_put_url`
is monkeypatched — no real R2 network calls in tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.routers.tally_agent as tally_agent_router
import app.services.tally.installer as installer_mod
from app.db import SessionLocal
from app.main import app
from app.models import AgentOutboxItem
from tools.make_backup_shop import run as make_shop
from tools.make_platform_admin import run as make_admin


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


_last_built_key: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _stub_installer_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provisioning also builds + caches an installer zip — stub it out (no
    real agent build dir / R2 in tests). The plaintext key normally never
    leaves this function (it's baked into the zip); tests that need to prove
    the key works read it back via `_last_built_key` rather than the API
    response, since the response no longer carries it."""
    _last_built_key.clear()

    def _fake_build(session, shop, *, plaintext_key):  # type: ignore[no-untyped-def]
        shop.installer_r2_key = f"installers/{shop.id}.zip"
        session.flush()
        _last_built_key[shop.id] = plaintext_key
        return shop.installer_r2_key

    monkeypatch.setattr(installer_mod, "build_and_cache_installer", _fake_build)
    import app.routers.admin as admin_router

    monkeypatch.setattr(admin_router, "get_object", lambda r2_key: b"PK\x03\x04fake-zip")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin_token(client: TestClient, email: str = "ops@fleek.example.com") -> str:
    with SessionLocal() as s:
        make_admin(s, email=email, password="ops-s3cret-pass")
        s.commit()
    r = client.post("/api/auth/login", json={"email": email, "password": "ops-s3cret-pass"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _make_shop(session: Session, name: str = "Sugal Foods") -> tuple[str, str]:
    """Returns (shop_id, plaintext_api_key)."""
    shop_id, key, _created = make_shop(session, name=name, tenant_id=None, rotate_key=False)
    session.commit()
    assert key is not None
    return shop_id, key


@pytest.fixture(autouse=True)
def _stub_presigned_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tally_agent_router,
        "presigned_put_url",
        lambda r2_key: (f"https://r2.example.com/{r2_key}?sig=stub", 900),
    )


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


def test_checkin_requires_shop_key(client: TestClient) -> None:
    r = client.post("/api/tally-agent/checkin", json={})
    assert r.status_code == 401


def test_checkin_rejects_unknown_key(client: TestClient) -> None:
    r = client.post(
        "/api/tally-agent/checkin", headers={"X-Shop-Key": "not-a-real-key"}, json={}
    )
    assert r.status_code == 401


def test_admin_shops_requires_platform_admin(client: TestClient, session: Session) -> None:
    _make_shop(session)
    r = client.get("/api/tally-agent/admin/shops")
    assert r.status_code == 401  # no auth at all

    # a normal (non-admin) firm login is forbidden too
    reg = client.post(
        "/api/auth/register",
        json={"firm_name": "Some Firm", "email": "owner@some-firm.example.com", "password": "pw123456"},
    )
    assert reg.status_code == 201, reg.text
    r = client.get("/api/tally-agent/admin/shops", headers=_auth(reg.json()["access_token"]))
    assert r.status_code == 403


# --------------------------------------------------------------------------
# checkin
# --------------------------------------------------------------------------


def test_checkin_updates_last_checkin_and_reports_error(
    client: TestClient, session: Session
) -> None:
    _shop_id, key = _make_shop(session)

    r = client.post(
        "/api/tally-agent/checkin",
        headers={"X-Shop-Key": key},
        json={"module_status": {"backup": "ok"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["outbox"] == []

    r2 = client.post(
        "/api/tally-agent/checkin",
        headers={"X-Shop-Key": key},
        json={"module_status": {"backup": "error"}, "error": "watch folder missing"},
    )
    assert r2.status_code == 200

    admin_token = _admin_token(client)
    listed = client.get("/api/tally-agent/admin/shops", headers=_auth(admin_token)).json()
    assert len(listed) == 1
    assert listed[0]["last_error"] == "watch folder missing"
    assert listed[0]["last_checkin_at"] is not None


def test_checkin_records_tally_reachability(client: TestClient, session: Session) -> None:
    from app.models import BackupShop

    shop_id, key = _make_shop(session)

    r = client.post(
        "/api/tally-agent/checkin",
        headers={"X-Shop-Key": key},
        json={"tally_reachable": False, "tally_reason": "refused"},
    )
    assert r.status_code == 200
    session.expire_all()
    shop = session.get(BackupShop, shop_id)
    assert shop.last_tally_status == "refused"
    assert shop.last_tally_ok_at is None

    r = client.post(
        "/api/tally-agent/checkin",
        headers={"X-Shop-Key": key},
        json={"tally_reachable": True},
    )
    assert r.status_code == 200
    session.expire_all()
    shop = session.get(BackupShop, shop_id)
    assert shop.last_tally_status == "connected"
    assert shop.last_tally_ok_at is not None

    # a checkin that doesn't mention tally_reachable leaves it alone
    prev_status, prev_ok_at = shop.last_tally_status, shop.last_tally_ok_at
    r = client.post("/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={})
    assert r.status_code == 200
    session.expire_all()
    shop = session.get(BackupShop, shop_id)
    assert shop.last_tally_status == prev_status
    assert shop.last_tally_ok_at == prev_ok_at


def test_checkin_returns_queued_outbox_items(client: TestClient, session: Session) -> None:
    shop_id, key = _make_shop(session)
    session.add(
        AgentOutboxItem(
            shop_id=shop_id, module="whatsapp_delivery", payload={"voucher": "V1"}, status="queued"
        )
    )
    session.add(
        AgentOutboxItem(
            shop_id=shop_id, module="whatsapp_delivery", payload={"voucher": "V2"}, status="sent"
        )
    )
    session.commit()

    r = client.post("/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={})
    assert r.status_code == 200
    outbox = r.json()["outbox"]
    assert len(outbox) == 1
    assert outbox[0]["payload"] == {"voucher": "V1"}


# --------------------------------------------------------------------------
# upload flow
# --------------------------------------------------------------------------


def test_upload_request_then_confirm(client: TestClient, session: Session) -> None:
    from app.models import BackupUpload

    _shop_id, key = _make_shop(session)

    req = client.post(
        "/api/tally-agent/upload-request",
        headers={"X-Shop-Key": key},
        json={
            "filename": "TDBK1800_100000.001",
            "size_bytes": 12345,
            "set_id": "tbk:1800_100000_v0",
        },
    )
    assert req.status_code == 200, req.text
    body = req.json()
    assert body["put_url"].startswith("https://r2.example.com/")
    assert body["r2_key"].endswith("_TDBK1800_100000.001")
    upload_id = body["upload_id"]

    session.expire_all()
    stored = session.get(BackupUpload, upload_id)
    assert stored is not None
    assert stored.set_id == "tbk:1800_100000_v0"

    confirm = client.post(
        "/api/tally-agent/upload-confirm",
        headers={"X-Shop-Key": key},
        json={"upload_id": upload_id, "status": "confirmed"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "confirmed"

    admin_token = _admin_token(client)
    listed = client.get("/api/tally-agent/admin/shops", headers=_auth(admin_token)).json()
    assert listed[0]["upload_count"] == 1
    assert listed[0]["last_upload_at"] is not None


def test_upload_confirm_unknown_id_404s(client: TestClient, session: Session) -> None:
    _shop_id, key = _make_shop(session)
    r = client.post(
        "/api/tally-agent/upload-confirm",
        headers={"X-Shop-Key": key},
        json={"upload_id": "does-not-exist", "status": "confirmed"},
    )
    assert r.status_code == 404


def test_upload_confirm_scoped_to_owning_shop(client: TestClient, session: Session) -> None:
    _shop_a_id, key_a = _make_shop(session, name="Shop A")
    _shop_b_id, key_b = _make_shop(session, name="Shop B")

    req = client.post(
        "/api/tally-agent/upload-request",
        headers={"X-Shop-Key": key_a},
        json={"filename": "a.001", "size_bytes": 1},
    )
    upload_id = req.json()["upload_id"]

    # Shop B must not be able to confirm shop A's upload.
    r = client.post(
        "/api/tally-agent/upload-confirm",
        headers={"X-Shop-Key": key_b},
        json={"upload_id": upload_id, "status": "confirmed"},
    )
    assert r.status_code == 404


def test_inactive_shop_key_rejected(client: TestClient, session: Session) -> None:
    from app.models import BackupShop

    shop_id, key = _make_shop(session)
    shop = session.get(BackupShop, shop_id)
    assert shop is not None
    shop.is_active = False
    session.commit()

    r = client.post("/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={})
    assert r.status_code == 401


# --------------------------------------------------------------------------
# firm-scoped agent provisioning (Ops console)
# --------------------------------------------------------------------------


def _make_firm(client: TestClient, tok: str, name: str = "Sitha Steels") -> str:
    r = client.post("/api/admin/firms", headers=_auth(tok), json={"legal_name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_provision_firm_agent_returns_key_once(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)

    # none yet
    r = client.get(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["provisioned"] is False

    # provision -> agent identity created, installer built + cached; the key
    # itself is no longer echoed back (it's baked into the zip instead)
    r = client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(tok))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["shop_id"] and body["installer_ready"] is True
    shop_id = body["shop_id"]
    key = _last_built_key[shop_id]

    # status now shows provisioned + installer ready, no key
    r = client.get(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(tok))
    assert r.json() == {
        "provisioned": True,
        "shop_id": shop_id,
        "is_active": True,
        "last_checkin_at": None,
        "last_upload_at": None,
        "upload_count": 0,
        "installer_ready": True,
        "agent_online": False,
        "tally_status": None,
        "tally_ok_at": None,
    }

    # the key baked into the installer actually works for agent auth
    r = client.post(
        "/api/tally-agent/checkin", headers={"X-Shop-Key": key}, json={}
    )
    assert r.status_code == 200

    # the installer itself downloads
    r = client.get(f"/api/admin/firms/{firm_id}/tally-shop/installer", headers=_auth(tok))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    # second provision -> 409, rotate instead
    r = client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(tok))
    assert r.status_code == 409


def test_rotate_firm_agent_key_invalidates_old(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    r = client.post(f"/api/admin/firms/{firm_id}/tally-shop", headers=_auth(tok))
    shop_id = r.json()["shop_id"]
    old_key = _last_built_key[shop_id]

    r = client.post(
        f"/api/admin/firms/{firm_id}/tally-shop/rotate-key", headers=_auth(tok)
    )
    assert r.status_code == 200
    assert r.json()["installer_ready"] is True
    new_key = _last_built_key[shop_id]
    assert new_key != old_key

    # old key dead, new key works
    assert (
        client.post(
            "/api/tally-agent/checkin", headers={"X-Shop-Key": old_key}, json={}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/tally-agent/checkin", headers={"X-Shop-Key": new_key}, json={}
        ).status_code
        == 200
    )


def test_rotate_without_agent_is_404(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    r = client.post(
        f"/api/admin/firms/{firm_id}/tally-shop/rotate-key", headers=_auth(tok)
    )
    assert r.status_code == 404

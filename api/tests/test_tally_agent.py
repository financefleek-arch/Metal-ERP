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
from app.db import SessionLocal
from app.main import app
from app.models import AgentOutboxItem
from tools.make_backup_shop import run as make_shop
from tools.make_platform_admin import run as make_admin


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
    _shop_id, key = _make_shop(session)

    req = client.post(
        "/api/tally-agent/upload-request",
        headers={"X-Shop-Key": key},
        json={"filename": "backup_20260903.001", "size_bytes": 12345},
    )
    assert req.status_code == 200, req.text
    body = req.json()
    assert body["put_url"].startswith("https://r2.example.com/")
    assert body["r2_key"].endswith("_backup_20260903.001")
    upload_id = body["upload_id"]

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

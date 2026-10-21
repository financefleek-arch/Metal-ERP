"""Fleek control-plane calls: signed requests, provisioning, linking (docs/PLAN-fleek-control-plane.md P1)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Synonym, Tenant, TenantWhatsappConfig
from app.services.control_plane_auth import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    check_signature,
    sign,
)

SECRET = "test-control-plane-secret"
PROVISION = "/api/internal/control-plane/provision"
LINK = "/api/internal/control-plane/link"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "control_plane_secret", SECRET)


def _call(
    client: TestClient,
    method: str,
    path: str,
    payload: dict,
    *,
    secret: str = SECRET,
    ts: int | None = None,
):
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()) if ts is None else ts)
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: sign(secret, timestamp=stamp, method=method, path=path, body=body),
    }
    return client.request(method, path, content=body, headers=headers)


def _post(client: TestClient, path: str, payload: dict, *, secret: str = SECRET, ts: int | None = None):
    return _call(client, "POST", path, payload, secret=secret, ts=ts)


# --------------------------------------------------------------------------
# signature check (pure)
# --------------------------------------------------------------------------


def test_signature_roundtrip_and_tamper() -> None:
    sig = sign(SECRET, timestamp="1000", method="POST", path="/p", body=b"{}")
    check_signature(SECRET, timestamp="1000", signature=sig, method="POST", path="/p", body=b"{}", now=1000)
    for kw in (
        {"body": b"{ }"},
        {"path": "/q"},
        {"method": "GET"},
    ):
        args = {"timestamp": "1000", "signature": sig, "method": "POST", "path": "/p", "body": b"{}", **kw}
        with pytest.raises(Exception) as exc:
            check_signature(SECRET, now=1000, **args)
        assert exc.value.status_code == 401


def test_stale_and_missing_and_unset_secret() -> None:
    sig = sign(SECRET, timestamp="1000", method="POST", path="/p", body=b"")
    with pytest.raises(Exception) as exc:
        check_signature(
            SECRET, timestamp="1000", signature=sig, method="POST", path="/p", body=b"", now=1000 + 301
        )
    assert exc.value.status_code == 401
    with pytest.raises(Exception) as exc:
        check_signature(SECRET, timestamp=None, signature=None, method="POST", path="/p", body=b"", now=1000)
    assert exc.value.status_code == 401
    with pytest.raises(Exception) as exc:
        check_signature(None, timestamp="1000", signature=sig, method="POST", path="/p", body=b"", now=1000)
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


def test_unsigned_and_wrong_secret_are_refused(client: TestClient) -> None:
    assert client.post(PROVISION, json={"fleek_org_id": "o1", "legal_name": "Shop One"}).status_code == 401
    assert (
        _post(client, PROVISION, {"fleek_org_id": "o1", "legal_name": "Shop One"}, secret="nope").status_code
        == 401
    )
    assert _post(client, PROVISION, {"fleek_org_id": "o1", "legal_name": "Shop One"}, ts=1).status_code == 401
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Tenant)) == 0


def test_routes_closed_when_secret_unset(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "control_plane_secret", None)
    r = _post(client, PROVISION, {"fleek_org_id": "o1", "legal_name": "Shop One"})
    assert r.status_code == 503


def test_provision_creates_a_seeded_firm_once(client: TestClient) -> None:
    r = _post(
        client, PROVISION, {"fleek_org_id": "org-1", "legal_name": "Kumar Steel House", "city": "Kolkata"}
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["created"] is True and first["fleek_org_id"] == "org-1"

    again = _post(client, PROVISION, {"fleek_org_id": "org-1", "legal_name": "Renamed Meanwhile"}).json()
    assert again["created"] is False and again["tenant_id"] == first["tenant_id"]
    assert again["legal_name"] == "Kumar Steel House"

    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Tenant)) == 1
        firm = s.get(Tenant, first["tenant_id"])
        assert firm is not None and firm.fleek_org_id == "org-1" and firm.city == "Kolkata"
        assert s.scalar(select(func.count()).select_from(Synonym).where(Synonym.tenant_id == firm.id)) > 0


def test_provision_validates_input(client: TestClient) -> None:
    assert _post(client, PROVISION, {"fleek_org_id": "", "legal_name": "Shop"}).status_code == 422
    assert _post(client, PROVISION, {"fleek_org_id": "o", "legal_name": "x"}).status_code == 422


def _make_firm(name: str) -> str:
    with SessionLocal() as s:
        t = Tenant(legal_name=name)
        s.add(t)
        s.commit()
        return t.id


def test_link_attaches_once_and_is_idempotent(client: TestClient) -> None:
    tid = _make_firm("Old Shop")
    ok = _post(client, LINK, {"tenant_id": tid, "fleek_org_id": "org-7"})
    assert ok.status_code == 200 and ok.json()["created"] is False
    assert _post(client, LINK, {"tenant_id": tid, "fleek_org_id": "org-7"}).status_code == 200
    with SessionLocal() as s:
        assert s.get(Tenant, tid).fleek_org_id == "org-7"


def test_link_refuses_moves_and_unknowns(client: TestClient) -> None:
    a, b = _make_firm("Shop A"), _make_firm("Shop B")
    assert _post(client, LINK, {"tenant_id": a, "fleek_org_id": "org-1"}).status_code == 200
    assert (
        _post(client, LINK, {"tenant_id": a, "fleek_org_id": "org-2"}).status_code == 409
    )  # firm already linked
    assert (
        _post(client, LINK, {"tenant_id": b, "fleek_org_id": "org-1"}).status_code == 409
    )  # org already linked
    assert _post(client, LINK, {"tenant_id": "missing", "fleek_org_id": "org-3"}).status_code == 404


def test_operator_console_still_creates_firms(client: TestClient) -> None:
    from tools.make_platform_admin import run as make_admin

    with SessionLocal() as s:
        make_admin(s, email="ops@fleek.example.com", password="ops-s3cret-pass")
        s.commit()
    tok = client.post(
        "/api/auth/login", json={"email": "ops@fleek.example.com", "password": "ops-s3cret-pass"}
    ).json()["access_token"]
    r = client.post(
        "/api/admin/firms",
        json={"legal_name": "Console Firm", "city": "Pune"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201, r.text
    with SessionLocal() as s:
        firm = s.get(Tenant, r.json()["id"])
        assert firm is not None and firm.fleek_org_id is None
        assert s.scalar(select(func.count()).select_from(Synonym).where(Synonym.tenant_id == firm.id)) > 0


WHATSAPP = "/api/internal/control-plane/whatsapp"


def _cfg(org: str, **kw: object) -> dict:
    return {
        "fleek_org_id": org,
        "phone_number_id": "pn-1",
        "waba_id": "waba-1",
        "display_phone_number": "+91 98000 00000",
        "is_active": True,
        **kw,
    }


def test_whatsapp_config_is_created_then_updated_in_place(client: TestClient) -> None:
    tid = _make_firm("Shop A")
    assert _post(client, LINK, {"tenant_id": tid, "fleek_org_id": "org-w"}).status_code == 200
    first = _call(client, "PUT", WHATSAPP, _cfg("org-w"))
    assert first.status_code == 200, first.text
    assert first.json()["tenant_id"] == tid and first.json()["is_active"] is True
    again = _call(client, "PUT", WHATSAPP, _cfg("org-w", phone_number_id="pn-2", is_active=False))
    assert again.status_code == 200
    with SessionLocal() as s:
        rows = list(s.scalars(select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == tid)))
        assert len(rows) == 1
        assert (rows[0].phone_number_id, rows[0].is_active, rows[0].waba_id) == ("pn-2", False, "waba-1")


def test_whatsapp_config_refuses_unknown_orgs_signature_less_calls_and_shared_numbers(
    client: TestClient,
) -> None:
    assert _call(client, "PUT", WHATSAPP, _cfg("nobody")).status_code == 404
    assert client.put(WHATSAPP, json=_cfg("nobody")).status_code == 401
    assert _call(client, "PUT", WHATSAPP, _cfg("org-x"), secret="bad").status_code == 401
    a, b = _make_firm("Shop A"), _make_firm("Shop B")
    _post(client, LINK, {"tenant_id": a, "fleek_org_id": "org-a"})
    _post(client, LINK, {"tenant_id": b, "fleek_org_id": "org-b"})
    assert _call(client, "PUT", WHATSAPP, _cfg("org-a")).status_code == 200
    taken = _call(client, "PUT", WHATSAPP, _cfg("org-b"))
    assert taken.status_code == 409
    inactive = _call(client, "PUT", WHATSAPP, _cfg("org-b", is_active=False))
    assert inactive.status_code == 200  # recording "no number" is never a clash

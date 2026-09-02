"""Platform-admin API: firm + user provisioning across tenants.

The operator ("platform admin") is the only principal that may act
outside its own tenant, and only on `/api/admin/*`. Everything else stays
strictly tenant-scoped.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Synonym, User
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
    r = client.post(
        "/api/auth/login", json={"email": email, "password": "ops-s3cret-pass"}
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _register_firm(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Existing Firm", "email": email, "password": "firm-pass-1"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


# --------------------------------------------------------------------------
# gating
# --------------------------------------------------------------------------


def test_normal_user_gets_403_on_every_admin_route(client: TestClient) -> None:
    token = _register_firm(client, "owner@firm-a.example.com")
    for method, path in [
        ("get", "/api/admin/firms"),
        ("post", "/api/admin/firms"),
        ("get", "/api/admin/firms/x"),
        ("patch", "/api/admin/firms/x"),
        ("post", "/api/admin/firms/x/users"),
        ("patch", "/api/admin/users/x"),
        ("delete", "/api/admin/users/x"),
    ]:
        r = client.request(method.upper(), path, headers=_auth(token), json={})
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


def test_me_surfaces_platform_admin_flag(client: TestClient) -> None:
    firm_token = _register_firm(client, "owner@firm-b.example.com")
    me = client.get("/api/auth/me", headers=_auth(firm_token)).json()
    assert me["is_platform_admin"] is False

    admin_token = _admin_token(client, "ops-me@fleek.example.com")
    me = client.get("/api/auth/me", headers=_auth(admin_token)).json()
    assert me["is_platform_admin"] is True


# --------------------------------------------------------------------------
# firms
# --------------------------------------------------------------------------


def test_create_firm_seeds_catalogue_and_has_no_user(client: TestClient) -> None:
    token = _admin_token(client)
    r = client.post(
        "/api/admin/firms",
        headers=_auth(token),
        json={"legal_name": "Brand New Metals", "city": "Jaipur"},
    )
    assert r.status_code == 201, r.text
    firm = r.json()
    assert firm["legal_name"] == "Brand New Metals"
    assert firm["city"] == "Jaipur"
    assert firm["users"] == []

    with SessionLocal() as s:
        syns = s.scalars(
            select(Synonym).where(Synonym.tenant_id == firm["id"])
        ).all()
        assert len(syns) > 0  # seed_synonyms ran


def test_list_and_get_firm(client: TestClient) -> None:
    token = _admin_token(client)
    _register_firm(client, "owner@listed.example.com")  # a second firm exists

    made = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Zzz Traders"}
    ).json()

    listed = client.get("/api/admin/firms", headers=_auth(token))
    assert listed.status_code == 200
    names = [f["legal_name"] for f in listed.json()]
    assert "Zzz Traders" in names
    assert "Existing Firm" in names

    # search filter
    filtered = client.get("/api/admin/firms?q=zzz", headers=_auth(token)).json()
    assert [f["legal_name"] for f in filtered] == ["Zzz Traders"]

    detail = client.get(f"/api/admin/firms/{made['id']}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["legal_name"] == "Zzz Traders"


def test_patch_firm_fields_and_flags(client: TestClient) -> None:
    token = _admin_token(client)
    firm = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Flag Co"}
    ).json()

    r = client.patch(
        f"/api/admin/firms/{firm['id']}",
        headers=_auth(token),
        json={"city": "Delhi", "ext_inward_import": True, "gst_enabled": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["city"] == "Delhi"
    assert out["ext_inward_import"] is True
    assert out["gst_enabled"] is True


def test_get_missing_firm_404(client: TestClient) -> None:
    token = _admin_token(client)
    assert client.get("/api/admin/firms/nope", headers=_auth(token)).status_code == 404


# --------------------------------------------------------------------------
# firm users
# --------------------------------------------------------------------------


def test_created_user_logs_in_and_is_scoped_to_its_firm(client: TestClient) -> None:
    token = _admin_token(client)
    firm_a = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Firm Alpha"}
    ).json()
    firm_b = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Firm Beta"}
    ).json()

    r = client.post(
        f"/api/admin/firms/{firm_a['id']}/users",
        headers=_auth(token),
        json={"email": "ramesh@alpha.example.com", "password": "ramesh-pass-1", "role": "owner"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "owner"
    assert "password" not in r.json()
    assert "password_hash" not in r.json()

    login = client.post(
        "/api/auth/login",
        json={"email": "ramesh@alpha.example.com", "password": "ramesh-pass-1"},
    )
    assert login.status_code == 200
    user_token = login.json()["access_token"]

    me = client.get("/api/auth/me", headers=_auth(user_token)).json()
    assert me["tenant_id"] == firm_a["id"]
    assert me["tenant_id"] != firm_b["id"]
    assert me["is_platform_admin"] is False


def test_add_user_duplicate_email_rejected_across_firms(client: TestClient) -> None:
    token = _admin_token(client)
    _register_firm(client, "taken@firm.example.com")
    firm = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Dup Co"}
    ).json()

    r = client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={"email": "taken@firm.example.com", "password": "whatever-1", "role": "accountant"},
    )
    assert r.status_code == 409


def test_add_user_short_password_422(client: TestClient) -> None:
    token = _admin_token(client)
    firm = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Short Pw Co"}
    ).json()
    r = client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={"email": "x@short.example.com", "password": "short", "role": "viewer"},
    )
    assert r.status_code == 422


def test_reset_password_invalidates_old_and_accepts_new(client: TestClient) -> None:
    token = _admin_token(client)
    firm = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Reset Co"}
    ).json()
    user = client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={"email": "reset@co.example.com", "password": "old-pass-11", "role": "accountant"},
    ).json()

    r = client.patch(
        f"/api/admin/users/{user['id']}",
        headers=_auth(token),
        json={"password": "new-pass-22"},
    )
    assert r.status_code == 200

    assert client.post(
        "/api/auth/login",
        json={"email": "reset@co.example.com", "password": "old-pass-11"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "reset@co.example.com", "password": "new-pass-22"},
    ).status_code == 200


def test_disable_user_kills_token(client: TestClient) -> None:
    token = _admin_token(client)
    firm = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Disable Co"}
    ).json()
    client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={"email": "owner@disable.example.com", "password": "owner-pass-1", "role": "owner"},
    )
    user = client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={
            "email": "clerk@disable.example.com",
            "password": "clerk-pass-1",
            "role": "accountant",
        },
    ).json()

    clerk_token = client.post(
        "/api/auth/login",
        json={"email": "clerk@disable.example.com", "password": "clerk-pass-1"},
    ).json()["access_token"]
    assert client.get("/api/auth/me", headers=_auth(clerk_token)).status_code == 200

    d = client.delete(f"/api/admin/users/{user['id']}", headers=_auth(token))
    assert d.status_code == 204
    assert client.get("/api/auth/me", headers=_auth(clerk_token)).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "clerk@disable.example.com", "password": "clerk-pass-1"},
    ).status_code == 403


def test_last_active_owner_cannot_be_disabled_or_demoted(client: TestClient) -> None:
    token = _admin_token(client)
    firm = client.post(
        "/api/admin/firms", headers=_auth(token), json={"legal_name": "Only Owner Co"}
    ).json()
    owner = client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={"email": "solo@owner.example.com", "password": "solo-pass-1", "role": "owner"},
    ).json()

    # demote -> blocked
    r = client.patch(
        f"/api/admin/users/{owner['id']}",
        headers=_auth(token),
        json={"role": "accountant"},
    )
    assert r.status_code == 409

    # deactivate via PATCH -> blocked
    r = client.patch(
        f"/api/admin/users/{owner['id']}",
        headers=_auth(token),
        json={"is_active": False},
    )
    assert r.status_code == 409

    # deactivate via DELETE -> blocked
    assert client.delete(
        f"/api/admin/users/{owner['id']}", headers=_auth(token)
    ).status_code == 409

    # add a second owner -> now the first can be demoted
    client.post(
        f"/api/admin/firms/{firm['id']}/users",
        headers=_auth(token),
        json={"email": "second@owner.example.com", "password": "second-pass-1", "role": "owner"},
    )
    r = client.patch(
        f"/api/admin/users/{owner['id']}",
        headers=_auth(token),
        json={"role": "accountant"},
    )
    assert r.status_code == 200


def test_cannot_touch_platform_admin_via_admin_api(client: TestClient) -> None:
    token = _admin_token(client, "ops-guard@fleek.example.com")
    with SessionLocal() as s:
        admin_user = s.scalar(
            select(User).where(User.email == "ops-guard@fleek.example.com")
        )
        admin_id = admin_user.id

    assert client.patch(
        f"/api/admin/users/{admin_id}",
        headers=_auth(token),
        json={"is_active": False},
    ).status_code == 403
    assert client.delete(
        f"/api/admin/users/{admin_id}", headers=_auth(token)
    ).status_code == 403


def test_make_platform_admin_is_idempotent_and_reuses_ops_tenant(client: TestClient) -> None:
    with SessionLocal() as s:
        id1, created1 = make_admin(s, email="idem@fleek.example.com", password="idem-pass-1")
        s.commit()
    with SessionLocal() as s:
        id2, created2 = make_admin(s, email="idem@fleek.example.com", password=None)
        s.commit()
    assert id1 == id2
    assert created1 is True
    assert created2 is False

    with SessionLocal() as s:
        from app.models import Tenant

        ops = s.scalars(
            select(Tenant).where(Tenant.legal_name == "Fleek Operations")
        ).all()
        assert len(ops) == 1

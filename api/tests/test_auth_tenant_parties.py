"""End-to-end API tests: register a firm -> log in -> onboard -> parties CRUD.

Runs against the app with its engine pointed at the conftest SQLite DB.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str = "owner@sethia.example.com") -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me_flow(client: TestClient) -> None:
    token = _register(client, "flow@sethia.example.com")

    # duplicate email rejected
    dup = client.post(
        "/api/auth/register",
        json={"firm_name": "X", "email": "flow@sethia.example.com", "password": "another-pass"},
    )
    assert dup.status_code == 409

    # login returns a token
    login = client.post(
        "/api/auth/login",
        json={"email": "flow@sethia.example.com", "password": "s3cret-pass"},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"

    # wrong password
    bad = client.post(
        "/api/auth/login",
        json={"email": "flow@sethia.example.com", "password": "nope"},
    )
    assert bad.status_code == 401

    me = client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "flow@sethia.example.com"
    assert body["role"] == "owner"


def test_protected_routes_require_token(client: TestClient) -> None:
    assert client.get("/api/tenant").status_code == 401  # no bearer header
    assert client.get("/api/parties").status_code == 401
    assert client.get("/api/tenant", headers=_auth("garbage")).status_code == 401


def test_tenant_read_and_onboard(client: TestClient) -> None:
    token = _register(client, "firm@sethia.example.com")

    got = client.get("/api/tenant", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["legal_name"] == "Sethia Metal Store"
    assert got.json()["document_label"] == "Invoice"
    assert got.json()["gst_enabled"] is False

    patched = client.patch(
        "/api/tenant",
        headers=_auth(token),
        json={
            "address": "Near Swastika Sporting Club, Dangipara",
            "city": "Siliguri",
            "state_code": "19",
            "pincode": "734001",
            "phone": "9851503336",
            "pan": "ACHPJ4356R",
            "bank_name": "KOTAK BANK",
            "bank_ac_no": "0813702740",
            "bank_ifsc": "KKBK0006749",
            "document_label": "Bill of Supply",
        },
    )
    assert patched.status_code == 200
    b = patched.json()
    assert b["city"] == "Siliguri"
    assert b["state_code"] == "19"
    assert b["document_label"] == "Bill of Supply"
    assert b["bank_ifsc"] == "KKBK0006749"


def test_party_crud_and_tenant_isolation(client: TestClient) -> None:
    t1 = _register(client, "t1@x.example.com")
    t2 = _register(client, "t2@y.example.com")

    # create with an address
    created = client.post(
        "/api/parties",
        headers=_auth(t1),
        json={
            "legal_name": "Jay Matadee Enterprises",
            "role": "customer",
            "default_state_code": "19",
            "addresses": [
                {
                    "type": "both",
                    "line1": "Nilkanth Apartment, Millanpally",
                    "city": "Darjeeling",
                    "state_code": "19",
                    "is_default": True,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert created.json()["addresses"][0]["city"] == "Darjeeling"

    # duplicate name in same tenant -> 409
    dup = client.post(
        "/api/parties",
        headers=_auth(t1),
        json={"legal_name": "jay matadee enterprises"},
    )
    assert dup.status_code == 409

    # list + search
    lst = client.get("/api/parties", headers=_auth(t1))
    assert lst.status_code == 200 and len(lst.json()) == 1
    assert client.get("/api/parties?q=matadee", headers=_auth(t1)).json()[0]["id"] == pid
    assert client.get("/api/parties?q=zzz", headers=_auth(t1)).json() == []
    assert client.get("/api/parties?role=supplier", headers=_auth(t1)).json() == []

    # tenant 2 can't see or touch tenant 1's party
    assert client.get("/api/parties", headers=_auth(t2)).json() == []
    assert client.get(f"/api/parties/{pid}", headers=_auth(t2)).status_code == 404
    assert client.delete(f"/api/parties/{pid}", headers=_auth(t2)).status_code == 404

    # update (replaces addresses)
    upd = client.patch(
        f"/api/parties/{pid}",
        headers=_auth(t1),
        json={"phone": "98320 11223", "role": "both", "addresses": []},
    )
    assert upd.status_code == 200
    assert upd.json()["phone"] == "98320 11223"
    assert upd.json()["role"] == "both"
    assert upd.json()["addresses"] == []

    # delete
    assert client.delete(f"/api/parties/{pid}", headers=_auth(t1)).status_code == 204
    assert client.get(f"/api/parties/{pid}", headers=_auth(t1)).status_code == 404


def test_viewer_role_cannot_write(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    """A viewer can read but not create parties."""
    from sqlalchemy import select

    from app.models import User
    from app.models._mixins import UserRole
    from app.security import create_access_token

    token = _register(client, "downgrade@x.example.com")
    me = client.get("/api/auth/me", headers=_auth(token)).json()

    # flip the role directly and mint a viewer token
    u = session.scalar(select(User).where(User.id == me["id"]))
    u.role = UserRole.viewer
    session.commit()
    viewer_token = create_access_token(
        user_id=u.id, tenant_id=u.tenant_id, role=UserRole.viewer.value
    )

    assert client.get("/api/parties", headers=_auth(viewer_token)).status_code == 200
    blocked = client.post(
        "/api/parties", headers=_auth(viewer_token), json={"legal_name": "Nope Traders"}
    )
    assert blocked.status_code == 403

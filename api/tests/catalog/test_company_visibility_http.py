"""S1.7 - a company hidden from a role is invisible on every Tally surface that role can reach."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import User
from app.security import hash_password
from app.services.tally.companies import add_company

_PW = "s3cret-pass"


def _colleague(client: TestClient, tid: str, role: str) -> dict[str, str]:
    email = f"{role}-vis@x.example.com"
    with SessionLocal() as s:
        s.add(User(tenant_id=tid, email=email, password_hash=hash_password(_PW), role=role))
        s.commit()
    r = client.post("/api/auth/login", json={"email": email, "password": _PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_a_hidden_default_company_is_invisible_on_every_surface(catalog_client) -> None:  # type: ignore[no-untyped-def]
    client, owner, tid = catalog_client
    with SessionLocal() as s:
        c = add_company(s, tid, "Estimate", kind="estimate")
        c.visible_roles = ["owner"]  # hidden from the accountant
        s.commit()
    acct = _colleague(client, tid, "accountant")
    from tests.test_tally_links import _item

    with SessionLocal() as s:
        item_id = _item(s, tid).id
        s.commit()
    body = {"selection": {"ids": [item_id]}}

    # the owner reaches every surface (a 404 here would make the checks below meaningless)
    assert client.get("/api/supplier-catalogs/tally/settings", headers=owner).json()["company_name"] == "Estimate"
    assert client.get("/api/item-tally/run", headers=owner).status_code == 200
    assert client.post("/api/item-tally/preflight", headers=owner, json=body).status_code == 200

    # the accountant sees an unconnected firm, and cannot act
    settings = client.get("/api/supplier-catalogs/tally/settings", headers=acct)
    assert settings.status_code == 200
    assert settings.json()["connected"] is False and settings.json()["company_name"] is None
    assert client.get("/api/supplier-catalogs/tally/check", headers=acct).json() is None
    assert client.post("/api/supplier-catalogs/tally/check", headers=acct).status_code == 404
    put = client.put("/api/supplier-catalogs/tally/settings", headers=acct, json={"stock_group_root": "X"})
    assert put.status_code == 404
    assert client.post("/api/item-tally/preflight", headers=acct, json=body).status_code == 404
    assert client.post("/api/item-tally/push", headers=acct, json=body).status_code == 404
    assert client.get("/api/item-tally/run", headers=acct).status_code == 404


def test_a_company_open_to_the_role_stays_reachable(catalog_client) -> None:  # type: ignore[no-untyped-def]
    client, _owner, tid = catalog_client
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        c.visible_roles = ["accountant"]
        s.commit()
    acct = _colleague(client, tid, "accountant")
    assert client.get("/api/supplier-catalogs/tally/settings", headers=acct).json()["company_name"] == "GST"
    assert client.get("/api/item-tally/run", headers=acct).status_code == 200

"""S0 exit criteria: with ext_supplier_catalog OFF the module is invisible
(every route 404s); ON, the list route works. Tenant settings validate.

The 404 test must stay green forever: the module must never leak routes to a
tenant without the flag.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.catalog.conftest import auth, enable_catalog_flag, register


def test_flag_off_list_route_404s(client: TestClient) -> None:
    h = auth(register(client, "off@catalog.example.com"))
    r = client.get("/api/supplier-catalogs", headers=h)
    assert r.status_code == 404


def test_unauthenticated_is_401_not_404(client: TestClient) -> None:
    assert client.get("/api/supplier-catalogs").status_code == 401


def test_flag_on_list_is_empty(catalog_client: tuple[TestClient, dict[str, str], str]) -> None:
    client, h, _ = catalog_client
    r = client.get("/api/supplier-catalogs", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_me_exposes_the_flag(client: TestClient) -> None:
    token = register(client, "me@catalog.example.com")
    assert client.get("/api/auth/me", headers=auth(token)).json()["ext_supplier_catalog"] is False
    enable_catalog_flag(client, token)
    assert client.get("/api/auth/me", headers=auth(token)).json()["ext_supplier_catalog"] is True


def test_flag_is_per_tenant(client: TestClient) -> None:
    on = register(client, "a@catalog.example.com")
    off = register(client, "b@catalog.example.com")
    enable_catalog_flag(client, on)
    assert client.get("/api/supplier-catalogs", headers=auth(on)).status_code == 200
    assert client.get("/api/supplier-catalogs", headers=auth(off)).status_code == 404


# --- tenant settings ---------------------------------------------------------


def test_new_tenant_defaults(client: TestClient) -> None:
    h = auth(register(client, "def@catalog.example.com"))
    t = client.get("/api/tenant", headers=h).json()
    assert t["catalog_code_prefix"] is None
    assert t["catalog_group_create_policy"] == "auto"


def test_patch_prefix_is_uppercased_and_policy_saved(client: TestClient) -> None:
    h = auth(register(client, "set@catalog.example.com"))
    r = client.patch(
        "/api/tenant",
        headers=h,
        json={"catalog_code_prefix": " gl ", "catalog_group_create_policy": "suggest_only"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["catalog_code_prefix"] == "GL"
    assert r.json()["catalog_group_create_policy"] == "suggest_only"
    # persisted
    t = client.get("/api/tenant", headers=h).json()
    assert (t["catalog_code_prefix"], t["catalog_group_create_policy"]) == ("GL", "suggest_only")


def test_blank_prefix_clears_it(client: TestClient) -> None:
    h = auth(register(client, "clr@catalog.example.com"))
    client.patch("/api/tenant", headers=h, json={"catalog_code_prefix": "GL"})
    r = client.patch("/api/tenant", headers=h, json={"catalog_code_prefix": "  "})
    assert r.status_code == 200
    assert r.json()["catalog_code_prefix"] is None


def test_bad_prefix_and_policy_rejected(client: TestClient) -> None:
    h = auth(register(client, "bad@catalog.example.com"))
    for bad in ("G", "TOOLONGPFX", "G L", "G-L", "ग्ल"):
        r = client.patch("/api/tenant", headers=h, json={"catalog_code_prefix": bad})
        assert r.status_code == 422, bad
    r = client.patch("/api/tenant", headers=h, json={"catalog_group_create_policy": "always"})
    assert r.status_code == 422

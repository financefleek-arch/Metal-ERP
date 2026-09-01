"""X0 exit criterion: with ext_inward_import OFF, the whole module is
invisible — every /api/inward-bills* route 404s. With it ON, routes work.

This test must stay green forever: the module must never leak routes to a
tenant without the flag.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.inward.conftest import auth, enable_inward_flag, register

_ROUTES = [
    ("GET", "/api/inward-bills"),
    ("GET", "/api/inward-bills/some-id"),
    ("GET", "/api/inward-bills/some-id/pdf"),
    ("GET", "/api/inward-bills/some-id/xml"),
    ("PATCH", "/api/inward-bills/some-id"),
    ("POST", "/api/inward-bills/some-id/re-extract"),
    ("POST", "/api/inward-bills/some-id/reject"),
    ("POST", "/api/inward-bills/some-id/approve"),
    ("GET", "/api/inward-bills/settings/ledgers"),
    ("PUT", "/api/inward-bills/settings/ledgers"),
]


def test_flag_off_every_route_404s(client: TestClient) -> None:
    h = auth(register(client, "flagoff@x.example.com"))
    for method, path in _ROUTES:
        r = client.request(method, path, headers=h, json={})
        assert r.status_code == 404, f"{method} {path} -> {r.status_code} (expected 404)"

    # upload too
    r = client.post(
        "/api/inward-bills",
        headers=h,
        files={"files": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 404


def test_flag_on_routes_reachable(client: TestClient) -> None:
    token = register(client, "flagon@x.example.com")
    enable_inward_flag(client, token)
    h = auth(token)

    # list works (empty)
    r = client.get("/api/inward-bills", headers=h)
    assert r.status_code == 200
    assert r.json() == []

    # settings auto-creates the row with defaults
    r = client.get("/api/inward-bills/settings/ledgers", headers=h)
    assert r.status_code == 200
    assert r.json()["purchase_ledger"] == "Purchase Accounts"
    assert r.json()["xml_encoding"] == "UTF-16"

    # an unknown bill id 404s (route exists, row doesn't)
    r = client.get("/api/inward-bills/no-such-bill", headers=h)
    assert r.status_code == 404
    assert "Bill not found" in r.text


def test_flag_off_isolation_is_per_tenant(client: TestClient) -> None:
    """Tenant A has the flag; tenant B does not. B still 404s."""
    ta = register(client, "tenant-a@x.example.com")
    enable_inward_flag(client, ta)
    tb = register(client, "tenant-b@x.example.com")  # no flag

    assert client.get("/api/inward-bills", headers=auth(ta)).status_code == 200
    assert client.get("/api/inward-bills", headers=auth(tb)).status_code == 404

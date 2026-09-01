"""Invoice draft CRUD + list + duplicate + delete guard.

SQLite fixture DB. WeasyPrint's native libs are absent on the dev/CI box,
so finalize renders best-effort and `pdf_status` comes back "failed" —
the finalize transaction still commits (asserted in test_invoice_finalize).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _party(client: TestClient, h: dict[str, str], name: str = "Jay Matadee Enterprises") -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _draft(client: TestClient, h: dict, party_id: str, **body: object) -> dict:
    payload = {"party_id": party_id, **body}
    r = client.post("/api/invoices", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------


def test_create_draft_defaults(client: TestClient) -> None:
    h = _h(_register(client, "inv1@x.example.com"))
    pid = _party(client, h)
    d = _draft(client, h, pid)

    assert d["status"] == "draft"
    assert d["number"] is None
    assert d["series"] == "Sales"
    assert d["fy"].count("-") == 1
    assert d["template_version"] == "v1-nongst"
    assert d["totals"]["grand_total"] == "0.00"
    assert "select a party" not in d["finalize_blockers"]
    assert "add at least one line with an item" in d["finalize_blockers"]


def test_put_replaces_lines_and_recomputes_totals(client: TestClient) -> None:
    h = _h(_register(client, "inv2@x.example.com"))
    pid = _party(client, h)
    d = _draft(client, h, pid)

    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={
            "invoice_discount": "125.40",
            "lines": [
                {"description": "SS Utensil", "quantity": "10", "unit_rate": "100.00"},
                {"description": "MS Angle 40x40", "quantity": "5", "unit_rate": "200.00"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["subtotal"] == "2000.00"
    assert body["totals"]["taxable_total"] == "1874.60"
    assert body["totals"]["round_off"] == "0.40"
    assert body["totals"]["grand_total"] == "1875.00"
    assert body["finalize_blockers"] == []
    assert [ln["sl_no"] for ln in body["lines"]] == [1, 2]


def test_line_needing_qty_or_rate_blocks_finalize(client: TestClient) -> None:
    h = _h(_register(client, "inv3@x.example.com"))
    pid = _party(client, h)
    d = _draft(client, h, pid)
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "SS Utensil", "quantity": "0", "unit_rate": "100"}]},
    )
    assert r.status_code == 200
    assert any("line 1" in b for b in r.json()["finalize_blockers"])


def test_list_filters_and_shape(client: TestClient) -> None:
    h = _h(_register(client, "inv4@x.example.com"))
    p1 = _party(client, h, "Alpha Traders")
    p2 = _party(client, h, "Beta Steel")
    _draft(client, h, p1)
    _draft(client, h, p2)

    r = client.get("/api/invoices", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 2
    row = r.json()[0]
    assert set(row) >= {"id", "number", "party_name", "grand_total", "status", "pdf_status"}

    r = client.get("/api/invoices", headers=h, params={"q": "beta"})
    assert [x["party_name"] for x in r.json()] == ["Beta Steel"]

    r = client.get("/api/invoices", headers=h, params={"status": "final"})
    assert r.json() == []


def test_duplicate_makes_a_fresh_draft(client: TestClient) -> None:
    h = _h(_register(client, "inv5@x.example.com"))
    pid = _party(client, h)
    d = _draft(client, h, pid)
    client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "SS Utensil", "quantity": "3", "unit_rate": "264"}]},
    )
    r = client.post(f"/api/invoices/{d['id']}/duplicate", headers=h)
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert new_id != d["id"]

    got = client.get(f"/api/invoices/{new_id}", headers=h).json()
    assert got["status"] == "draft"
    assert got["number"] is None
    assert got["lines"][0]["description"] == "SS Utensil"


def test_delete_only_drafts(client: TestClient) -> None:
    h = _h(_register(client, "inv6@x.example.com"))
    pid = _party(client, h)
    d = _draft(client, h, pid)
    r = client.delete(f"/api/invoices/{d['id']}", headers=h)
    assert r.status_code == 204
    assert client.get(f"/api/invoices/{d['id']}", headers=h).status_code == 404


def test_cross_tenant_isolation(client: TestClient) -> None:
    h1 = _h(_register(client, "t1@x.example.com"))
    h2 = _h(_register(client, "t2@x.example.com"))
    pid = _party(client, h1)
    d = _draft(client, h1, pid)
    assert client.get(f"/api/invoices/{d['id']}", headers=h2).status_code == 404
    # a party from another tenant cannot be attached
    r = client.post("/api/invoices", headers=h2, json={"party_id": pid})
    assert r.status_code == 404


def test_unknown_party_rejected(client: TestClient) -> None:
    h = _h(_register(client, "inv7@x.example.com"))
    r = client.post("/api/invoices", headers=h, json={"party_id": "nope"})
    assert r.status_code == 404

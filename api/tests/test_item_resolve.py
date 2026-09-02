"""POST /api/items/resolve — the invoice-line type-ahead helper.

Register seeds the bartan synonym dictionary, so an item created as
"Pital Balti No 3" normalizes to "brass bucket no 3" and a query for
"balti no 3" resolves to it via the exact rung (synonyms applied to both
sides). SQLite here: the fuzzy rung is skipped, exact/alias still work.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ItemAlias


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _reg(client: TestClient, email: str = "resolve@x.example.com") -> tuple[dict, str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Bartan Bhandar", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    me = client.get("/api/auth/me", headers=h).json()
    return h, me["tenant_id"]


def _resolve(client: TestClient, h: dict, desc: str, hsn: str | None = None) -> dict:
    url = f"/api/items/resolve?description={desc}"
    if hsn:
        url += f"&hsn={hsn}"
    r = client.post(url, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_exact_via_synonym(client: TestClient) -> None:
    h, _ = _reg(client)
    made = client.post(
        "/api/items",
        headers=h,
        json={"name": "Brass Bucket No 3", "default_rate": "480"},
    )
    assert made.status_code == 201, made.text
    item_id = made.json()["id"]

    # shopkeeper types the Hindi words; both sides normalize to "brass bucket no 3"
    res = _resolve(client, h, "pital balti no 3")
    assert res["method"] == "exact"
    assert res["confidence"] == 1.0
    assert len(res["candidates"]) == 1
    c = res["candidates"][0]
    assert c["id"] == item_id
    assert c["default_rate"] == "480.00"
    assert c["score"] == 1.0


def test_alias_hit(client: TestClient) -> None:
    h, tenant_id = _reg(client, "alias@x.example.com")
    made = client.post("/api/items", headers=h, json={"name": "Brass Bucket No 3"})
    item_id = made.json()["id"]

    # a learned alias for a wording that doesn't normalize to the item key
    from app.db import SessionLocal

    with SessionLocal() as s:
        s.add(
            ItemAlias(
                tenant_id=tenant_id,
                item_id=item_id,
                alias_text="tokna no 3",
                alias_normalized="tokna no 3",
            )
        )
        s.commit()

    res = _resolve(client, h, "tokna no 3")
    assert res["method"] == "alias"
    assert res["confidence"] == 0.98
    assert res["candidates"][0]["id"] == item_id
    assert res["candidates"][0]["score"] == 0.98


def test_no_match_sqlite_degrades_cleanly(client: TestClient) -> None:
    h, _ = _reg(client, "nomatch@x.example.com")
    client.post("/api/items", headers=h, json={"name": "Brass Bucket No 3"})

    res = _resolve(client, h, "completely unrelated widget")
    assert res["method"] is None
    assert res["candidates"] == []
    assert res["weak"] is False

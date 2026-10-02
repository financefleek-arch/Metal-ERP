"""Item-category delete + merge must keep supplier-catalog items consistent
(the catalog item -> category FK would otherwise break a delete)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.catalog.conftest import SAMPLE_PDF

Env = tuple[TestClient, dict[str, str], str]


def _upload(client: TestClient, h: dict[str, str]) -> dict[str, Any]:
    r = client.post(
        "/api/supplier-catalogs",
        headers=h,
        files={"file": ("g.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _cats(client: TestClient, h: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in client.get("/api/item-categories", headers=h).json()}


def _groups(client: TestClient, h: dict[str, str], cid: str) -> dict[str, int]:
    rows = client.get(f"/api/supplier-catalogs/{cid}/groups", headers=h).json()
    return {r["name"]: r["item_count"] for r in rows}


def test_deleting_a_category_leaves_its_catalog_items_ungrouped(catalog_client: Env) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h)
    mugs = _cats(client, h)["Beer Mugs"]
    n = _groups(client, h, cat["id"])["Beer Mugs"]

    r = client.request("DELETE", f"/api/item-categories/{mugs['id']}", headers=h, json={})
    assert r.status_code == 204

    groups = _groups(client, h, cat["id"])
    assert "Beer Mugs" not in groups
    assert groups["No group"] >= n  # the items are still there, just ungrouped
    assert cat["item_count"] == sum(groups.values())


def test_delete_can_reassign_catalog_items(catalog_client: Env) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h)
    cats = _cats(client, h)
    n = _groups(client, h, cat["id"])["Beer Mugs"]
    before = _groups(client, h, cat["id"])["Cups & Mugs"]

    r = client.request(
        "DELETE",
        f"/api/item-categories/{cats['Beer Mugs']['id']}",
        headers=h,
        json={"reassign_to": cats["Cups & Mugs"]["id"]},
    )
    assert r.status_code == 204
    assert _groups(client, h, cat["id"])["Cups & Mugs"] == before + n


def test_merge_moves_catalog_items_and_removes_the_source(catalog_client: Env) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h)
    cats = _cats(client, h)
    mugs, cups = cats["Beer Mugs"], cats["Cups & Mugs"]
    g = _groups(client, h, cat["id"])
    r = client.post(
        f"/api/item-categories/{mugs['id']}/merge", headers=h, json={"into": cups["id"]}
    )
    assert r.status_code == 200
    assert r.json()["id"] == cups["id"]
    after = _groups(client, h, cat["id"])
    assert "Beer Mugs" not in after
    assert after["Cups & Mugs"] == g["Beer Mugs"] + g["Cups & Mugs"]
    assert "Beer Mugs" not in _cats(client, h)


def test_merge_into_itself_or_a_stranger_is_rejected(catalog_client: Env) -> None:
    client, h, _ = catalog_client
    _upload(client, h)
    mugs = _cats(client, h)["Beer Mugs"]
    url = f"/api/item-categories/{mugs['id']}/merge"
    assert client.post(url, headers=h, json={"into": mugs["id"]}).status_code == 422
    assert client.post(url, headers=h, json={"into": "nope"}).status_code == 404
    stranger = "/api/item-categories/nope/merge"
    assert client.post(stranger, headers=h, json={"into": mugs["id"]}).status_code == 404

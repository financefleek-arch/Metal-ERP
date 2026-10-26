"""Sort groups that have no category: suggestions from the items' names, and the bulk move."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Positive Traders", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _group(client: TestClient, h: dict[str, str], name: str, items: list[str]) -> dict:
    g = client.post("/api/item-groups", headers=h, json={"name": name}).json()
    for n in items:
        r = client.post("/api/items", headers=h, json={"name": n, "group_id": g["id"]})
        assert r.status_code == 201, r.text
    return g  # type: ignore[no-any-return]


def _suggestions(client: TestClient, h: dict[str, str]) -> dict:
    r = client.get("/api/item-groups/uncategorised", headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _by_name(body: dict, name: str) -> dict:
    return next(s for s in body["suggestions"] if s["name"] == name)


def test_a_group_of_cookers_is_suggested_pressure_cookers(client: TestClient) -> None:
    h = _h(_token(client, "gs-1@x.example.com"))
    _group(
        client,
        h,
        "Mixed Bag",
        [f"Pressure Cooker {n} L Inner Lid" for n in (2, 3, 5, 7, 9)],
    )
    body = _suggestions(client, h)
    s = _by_name(body, "Mixed Bag")
    assert s["category_name"] == "Pressure Cookers"
    assert s["confidence"] >= 0.7
    assert s["item_count"] == 5
    assert body["groups"] >= 1 and body["items"] >= 5


def test_a_group_with_no_clear_answer_gets_no_category_but_a_brand_style_name(
    client: TestClient,
) -> None:
    h = _h(_token(client, "gs-2@x.example.com"))
    _group(client, h, "APEX", ["Apex Widget 1", "Apex Widget 2", "Apex Widget 3"])
    s = _by_name(_suggestions(client, h), "APEX")
    assert s["category_id"] is None
    assert s["new_category_name"] == "Apex"


def test_a_group_with_a_category_is_not_listed(client: TestClient) -> None:
    h = _h(_token(client, "gs-3@x.example.com"))
    cid = client.get("/api/item-categories", headers=h).json()[0]["id"]
    client.post("/api/item-groups", headers=h, json={"name": "Has One", "category_id": cid})
    names = {s["name"] for s in _suggestions(client, h)["suggestions"]}
    assert "Has One" not in names


def test_assign_moves_the_group_and_its_items_and_can_create_a_category(
    client: TestClient,
) -> None:
    h = _h(_token(client, "gs-4@x.example.com"))
    g = _group(client, h, "APEX", ["Apex Widget 1", "Apex Widget 2"])

    r = client.post(
        "/api/item-groups/assign",
        headers=h,
        json={"moves": [{"group_id": g["id"], "new_category_name": "Apex"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"groups": 1, "items": 2, "created_categories": ["Apex"]}

    detail = client.get(f"/api/item-groups/{g['id']}", headers=h).json()
    assert detail["category_name"] == "Apex"
    items = client.get("/api/items?q=Apex Widget", headers=h).json()
    assert {i["category_id"] for i in items} == {detail["category_id"]}

    # it no longer shows as uncategorised
    assert "APEX" not in {s["name"] for s in _suggestions(client, h)["suggestions"]}

    # a second move to the same name reuses the category
    r2 = client.post(
        "/api/item-groups/assign",
        headers=h,
        json={"moves": [{"group_id": g["id"], "new_category_name": "apex"}]},
    )
    assert r2.json()["created_categories"] == []


def test_assign_with_no_category_takes_the_group_out_again(client: TestClient) -> None:
    h = _h(_token(client, "gs-5@x.example.com"))
    g = _group(client, h, "TITAN", ["Titan Flask 1", "Titan Flask 2"])
    client.post(
        "/api/item-groups/assign",
        headers=h,
        json={"moves": [{"group_id": g["id"], "new_category_name": "Titan"}]},
    )
    r = client.post(
        "/api/item-groups/assign", headers=h, json={"moves": [{"group_id": g["id"]}]}
    )
    assert r.json()["items"] == 2
    assert client.get(f"/api/item-groups/{g['id']}", headers=h).json()["category_id"] is None
    items = client.get("/api/items?q=Titan Flask", headers=h).json()
    assert {i["category_id"] for i in items} == {None}


def test_assign_rejects_unknown_ids_and_other_firms(client: TestClient) -> None:
    h = _h(_token(client, "gs-6@x.example.com"))
    other = _h(_token(client, "gs-7@x.example.com"))
    g = _group(client, h, "PARAG", ["Parag Item 1"])

    assert (
        client.post(
            "/api/item-groups/assign",
            headers=h,
            json={"moves": [{"group_id": "nope", "new_category_name": "X"}]},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/item-groups/assign",
            headers=h,
            json={"moves": [{"group_id": g["id"], "category_id": "nope"}]},
        ).status_code
        == 404
    )
    # another firm cannot move this firm's group
    assert (
        client.post(
            "/api/item-groups/assign",
            headers=other,
            json={"moves": [{"group_id": g["id"], "new_category_name": "Stolen"}]},
        ).status_code
        == 404
    )
    assert client.get(f"/api/item-groups/{g['id']}", headers=h).json()["category_id"] is None

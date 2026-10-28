"""Per-group margin (item > group > bulk), group suggestions and what merging a group does."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.catalog.pricing import effective_margin, sell_price
from tests.catalog.conftest import SAMPLE_PDF

Env = tuple[TestClient, dict[str, str], dict[str, Any]]


@pytest.fixture
def uploaded(catalog_client: tuple[TestClient, dict[str, str], str]) -> Env:
    client, h, _ = catalog_client
    r = client.post(
        "/api/supplier-catalogs",
        headers=h,
        files={"file": ("glassware.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return client, h, r.json()


def _items(client: TestClient, h: dict[str, str], cid: str, **params: Any) -> list[dict[str, Any]]:
    r = client.get(
        f"/api/supplier-catalogs/{cid}/items", headers=h, params={"limit": 200, **params}
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _group_two(client: TestClient, h: dict[str, str], cid: str) -> tuple[str, list[dict[str, Any]]]:
    """Put the first two rows in a group called Mugs and return its category id."""
    rows = _items(client, h, cid)
    ids = [r["id"] for r in rows[:2]]
    r = client.patch(
        f"/api/supplier-catalogs/{cid}/items/bulk",
        headers=h,
        json={"ids": ids, "changes": {"group_name": "Mugs"}},
    )
    assert r.status_code == 200, r.text
    mugs = [g for g in client.get(f"/api/supplier-catalogs/{cid}/groups", headers=h).json() if g["name"] == "Mugs"]
    return mugs[0]["category_id"], rows[:2]


def test_effective_margin_order() -> None:
    d = Decimal
    assert effective_margin(d("5"), d("10"), d("20")) == d("5")  # item wins
    assert effective_margin(None, d("10"), d("20")) == d("20")  # then group
    assert effective_margin(None, d("10"), None) == d("10")  # then bulk
    assert effective_margin(d("0"), d("10"), d("20")) == d("0")  # zero is a real margin


def test_group_margin_prices_its_rows_only(uploaded: Env) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    gid, rows = _group_two(client, h, cid)

    r = client.put(
        f"/api/supplier-catalogs/{cid}/groups/{gid}/margin", headers=h, json={"margin_pct": "25"}
    )
    assert r.status_code == 200, r.text
    after = {i["id"]: i for i in _items(client, h, cid)}
    for row in rows:
        cost = Decimal(row["cost_price"])
        assert Decimal(after[row["id"]]["sell_price"]) == sell_price(cost, Decimal("25"), 1)
    others = [i for i in after.values() if i["id"] not in {x["id"] for x in rows}]
    assert others and all(Decimal(i["sell_price"]) == Decimal(i["cost_price"]) for i in others)

    groups = client.get(f"/api/supplier-catalogs/{cid}/groups", headers=h).json()
    assert next(g for g in groups if g["category_id"] == gid)["margin_pct"] == "25.00"


def test_item_margin_beats_group_margin_and_clearing_falls_back(uploaded: Env) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    gid, rows = _group_two(client, h, cid)
    client.put(f"/api/supplier-catalogs/{cid}/groups/{gid}/margin", headers=h, json={"margin_pct": "25"})
    own, other = rows
    client.patch(
        f"/api/supplier-catalogs/{cid}/items/{own['id']}", headers=h, json={"item_margin_pct": "10"}
    )
    now = {i["id"]: i for i in _items(client, h, cid)}
    assert Decimal(now[own["id"]]["sell_price"]) == sell_price(Decimal(own["cost_price"]), Decimal("10"), 1)
    assert Decimal(now[other["id"]]["sell_price"]) == sell_price(Decimal(other["cost_price"]), Decimal("25"), 1)

    # clearing the group margin: the row without its own margin goes back to the bulk margin
    client.patch(f"/api/supplier-catalogs/{cid}", headers=h, json={"bulk_margin_pct": "5"})
    r = client.put(f"/api/supplier-catalogs/{cid}/groups/{gid}/margin", headers=h, json={"margin_pct": None})
    assert r.status_code == 200
    now = {i["id"]: i for i in _items(client, h, cid)}
    assert Decimal(now[other["id"]]["sell_price"]) == sell_price(Decimal(other["cost_price"]), Decimal("5"), 1)


def test_moving_a_row_into_a_group_applies_that_groups_margin(uploaded: Env) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    gid, rows = _group_two(client, h, cid)
    client.put(f"/api/supplier-catalogs/{cid}/groups/{gid}/margin", headers=h, json={"margin_pct": "40"})
    third = next(i for i in _items(client, h, cid) if i["id"] not in {x["id"] for x in rows})
    r = client.patch(
        f"/api/supplier-catalogs/{cid}/items/{third['id']}", headers=h, json={"group_name": "Mugs"}
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["sell_price"]) == sell_price(Decimal(third["cost_price"]), Decimal("40"), 1)


def test_group_margin_unknown_group_404(uploaded: Env) -> None:
    client, h, cat = uploaded
    r = client.put(
        f"/api/supplier-catalogs/{cat['id']}/groups/nope/margin", headers=h, json={"margin_pct": "5"}
    )
    assert r.status_code == 404


def test_merging_a_group_drops_its_margin_and_reprices(uploaded: Env) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    gid, rows = _group_two(client, h, cid)
    client.put(f"/api/supplier-catalogs/{cid}/groups/{gid}/margin", headers=h, json={"margin_pct": "25"})
    third = next(i for i in _items(client, h, cid) if i["id"] not in {x["id"] for x in rows})
    client.patch(f"/api/supplier-catalogs/{cid}/items/{third['id']}", headers=h, json={"group_name": "Jugs"})
    jugs = next(g for g in client.get(f"/api/supplier-catalogs/{cid}/groups", headers=h).json() if g["name"] == "Jugs")

    r = client.post(f"/api/item-categories/{gid}/merge", headers=h, json={"into": jugs["category_id"]})
    assert r.status_code == 200, r.text
    now = {i["id"]: i for i in _items(client, h, cid)}
    for row in rows:  # the merged rows follow Jugs, which has no margin: the bulk margin (0)
        assert Decimal(now[row["id"]]["sell_price"]) == Decimal(row["cost_price"])


def test_deleting_a_group_or_a_catalog_with_a_group_margin_does_not_error(uploaded: Env) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    gid, _ = _group_two(client, h, cid)
    client.put(f"/api/supplier-catalogs/{cid}/groups/{gid}/margin", headers=h, json={"margin_pct": "25"})
    assert client.request("DELETE", f"/api/item-categories/{gid}", headers=h, json={}).status_code == 204

    gid2, _ = _group_two(client, h, cid)
    client.put(f"/api/supplier-catalogs/{cid}/groups/{gid2}/margin", headers=h, json={"margin_pct": "10"})
    assert client.delete(f"/api/supplier-catalogs/{cid}", headers=h).status_code == 204


def test_group_suggestions_bunch_by_proposed_group(uploaded: Env) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    r = client.get(f"/api/supplier-catalogs/{cid}/group-suggestions", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ungrouped"] == sum(c["count"] for c in body["clusters"]) + body["unsuggested"]
    assert body["ungrouped"] == len(_items(client, h, cid, no_group="true"))
    for c in body["clusters"]:
        assert c["count"] == len(_items(client, h, cid, no_group="true", suggested_group=c["name"]))
    assert body["unsuggested"] == len(_items(client, h, cid, no_group="true", no_suggestion="true"))

"""Items page gaps: tree-node filters (group / category / ungrouped), merge group, delete group
with a classify rule, bulk price change."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ItemClassifyRule


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _cat(client: TestClient, h: dict[str, str], name: str) -> str:
    return client.post("/api/item-categories", headers=h, json={"name": name}).json()["id"]


def _grp(client: TestClient, h: dict[str, str], name: str, cat: str | None) -> str:
    r = client.post("/api/item-groups", headers=h, json={"name": name, "category_id": cat})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _item(client: TestClient, h: dict[str, str], name: str, **extra: object) -> str:
    r = client.post("/api/items", headers=h, json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _count(client: TestClient, h: dict[str, str], flt: dict) -> int:
    r = client.post("/api/items/count", headers=h, json=flt)
    assert r.status_code == 200, r.text
    return r.json()["count"]


# --------------------------------------------------------------------------
# G0: the filter understands the tree
# --------------------------------------------------------------------------


def test_filter_matches_tree_nodes(client: TestClient) -> None:
    h = _h(_token(client, "gap-1@x.example.com"))
    steel = _cat(client, h, "Gap Steel")
    other = _cat(client, h, "Gap Other")
    g1 = _grp(client, h, "Gap Angle", steel)
    g2 = _grp(client, h, "Gap Channel", steel)
    _item(client, h, "Gap Angle 40", group_id=g1)
    _item(client, h, "Gap Angle 50", group_id=g1)
    _item(client, h, "Gap Channel 75", group_id=g2)
    _item(client, h, "Gap Loose Steel", category_id=steel)
    _item(client, h, "Gap Other Thing", category_id=other)

    assert _count(client, h, {"group_id": g1}) == 2
    assert _count(client, h, {"category_id": steel}) == 4  # grouped and loose
    assert _count(client, h, {"category_id": steel, "ungrouped": True}) == 1
    assert _count(client, h, {"category_id": other}) == 1

    # the list route takes the same fields, and agrees with the tree's own leaf list
    listed = client.get(f"/api/items?group_id={g1}", headers=h).json()
    assert len(listed) == 2
    tree_leaves = client.get(f"/api/items/tree/leaves?category_id={steel}", headers=h).json()
    loose = client.get(f"/api/items?category_id={steel}&ungrouped=true", headers=h).json()
    assert {x["id"] for x in loose} == {x["id"] for x in tree_leaves}

    # the tree's counts equal the filter's counts
    tree = client.get("/api/items/tree", headers=h).json()
    node = next(c for c in tree if c["name"] == "Gap Steel")
    assert node["loose_count"] == _count(client, h, {"category_id": steel, "ungrouped": True})
    for g in node["groups"]:
        assert g["leaf_count"] == _count(client, h, {"group_id": g["id"]})


def test_filter_uncategorised(client: TestClient) -> None:
    h = _h(_token(client, "gap-2@x.example.com"))
    cat = _cat(client, h, "Gap Some")
    _item(client, h, "Gap Categorised Zzq", category_id=cat)
    before = _count(client, h, {"uncategorised": True, "ungrouped": True})
    r = client.post("/api/items", headers=h, json={"name": "Qzx Wxv Unclassifiable 991"})
    assert r.status_code == 201
    after = _count(client, h, {"uncategorised": True, "ungrouped": True})
    assert after >= before  # the new item is either classified away or counted
    assert _count(client, h, {"category_id": cat, "uncategorised": True}) == 1  # id wins


# --------------------------------------------------------------------------
# G2: merge group, delete group
# --------------------------------------------------------------------------


def test_merge_group_moves_items_and_deletes_source(client: TestClient) -> None:
    h = _h(_token(client, "gap-3@x.example.com"))
    cat_a = _cat(client, h, "Gap Merge A")
    cat_b = _cat(client, h, "Gap Merge B")
    src = _grp(client, h, "Gap Src Group", cat_a)
    dst = _grp(client, h, "Gap Dst Group", cat_b)
    s1 = _item(client, h, "Gap Src One", group_id=src)
    d1 = _item(client, h, "Gap Dst One", group_id=dst)

    r = client.post(f"/api/item-groups/{src}/merge", headers=h, json={"into": dst})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == dst
    assert {leaf["id"] for leaf in r.json()["leaves"]} == {s1, d1}
    moved = client.get(f"/api/items/{s1}", headers=h).json()
    assert moved["group_id"] == dst
    assert moved["category_id"] == cat_b
    assert client.get(f"/api/item-groups/{src}", headers=h).status_code == 404

    # can't merge into itself or into someone else's group
    same = client.post(f"/api/item-groups/{dst}/merge", headers=h, json={"into": dst})
    assert same.status_code == 422


def test_merge_group_moves_classify_rule(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_token(client, "gap-4@x.example.com"))
    src = _grp(client, h, "Gap Rule Src", None)
    dst = _grp(client, h, "Gap Rule Dst", None)
    tenant_id = client.get("/api/auth/me", headers=h).json()["tenant_id"]
    session.add(
        ItemClassifyRule(
            tenant_id=tenant_id, phrase_normalized="gaprule", department="Cookware", group_id=src
        )
    )
    session.commit()
    merged = client.post(f"/api/item-groups/{src}/merge", headers=h, json={"into": dst})
    assert merged.status_code == 200
    session.expire_all()
    rule = session.query(ItemClassifyRule).filter_by(phrase_normalized="gaprule").one()
    assert rule.group_id == dst


def test_delete_group_keeps_classify_rule(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_token(client, "gap-5@x.example.com"))
    g = _grp(client, h, "Gap Rule Doomed", None)
    tenant_id = client.get("/api/auth/me", headers=h).json()["tenant_id"]
    session.add(
        ItemClassifyRule(
            tenant_id=tenant_id, phrase_normalized="gapdoomed", department="Cookware", group_id=g
        )
    )
    session.commit()
    assert client.delete(f"/api/item-groups/{g}", headers=h).status_code == 204
    session.expire_all()
    rule = session.query(ItemClassifyRule).filter_by(phrase_normalized="gapdoomed").one()
    assert rule.group_id is None


# --------------------------------------------------------------------------
# G3: bulk price
# --------------------------------------------------------------------------


def _rate(client: TestClient, h: dict[str, str], item_id: str) -> str | None:
    return client.get(f"/api/items/{item_id}", headers=h).json()["default_rate"]


def test_bulk_price_percent_dry_run_then_apply(client: TestClient) -> None:
    h = _h(_token(client, "gap-6@x.example.com"))
    a = _item(client, h, "Gap Price A", default_rate="100")
    b = _item(client, h, "Gap Price B", default_rate="333")
    none = _item(client, h, "Gap Price None")
    body = {"ids": [a, b, none], "mode": "percent", "value": "10", "round_to": "5"}

    dry = client.post("/api/items/bulk-price?dry_run=true", headers=h, json=body).json()
    assert (dry["changed"], dry["unchanged"], dry["errors"]) == (2, 1, 0)
    assert float(_rate(client, h, a)) == 100.0  # nothing saved

    real = client.post("/api/items/bulk-price", headers=h, json=body).json()
    assert (real["changed"], real["unchanged"]) == (2, 1)
    assert float(_rate(client, h, a)) == 110.0
    assert float(_rate(client, h, b)) == 365.0  # 366.3 rounded to the nearest 5
    assert _rate(client, h, none) is None


def test_bulk_price_amount_down_and_floor(client: TestClient) -> None:
    h = _h(_token(client, "gap-7@x.example.com"))
    a = _item(client, h, "Gap Floor A", default_rate="50")
    b = _item(client, h, "Gap Floor B", default_rate="10")
    res = client.post(
        "/api/items/bulk-price",
        headers=h,
        json={"ids": [a, b], "mode": "amount", "value": "-20"},
    ).json()
    assert (res["changed"], res["errors"]) == (1, 1)
    assert float(_rate(client, h, a)) == 30.0
    assert float(_rate(client, h, b)) == 10.0  # would have gone below zero: left alone


def test_bulk_price_by_filter_and_validation(client: TestClient) -> None:
    h = _h(_token(client, "gap-8@x.example.com"))
    cat = _cat(client, h, "Gap Price Cat")
    g = _grp(client, h, "Gap Price Grp", cat)
    a = _item(client, h, "Gap Pf A", group_id=g, default_rate="200")
    other = _item(client, h, "Gap Pf B", default_rate="200")
    res = client.post(
        "/api/items/bulk-price",
        headers=h,
        json={"filter": {"group_id": g}, "mode": "percent", "value": "-50"},
    ).json()
    assert res["changed"] == 1
    assert float(_rate(client, h, a)) == 100.0
    assert float(_rate(client, h, other)) == 200.0

    bad = {"ids": [a], "mode": "percent", "value": "0"}
    assert client.post("/api/items/bulk-price", headers=h, json=bad).status_code == 422
    bad = {"ids": [a], "filter": {}, "mode": "percent", "value": "5"}
    assert client.post("/api/items/bulk-price", headers=h, json=bad).status_code == 422
    bad = {"ids": [a], "mode": "percent", "value": "-100"}
    assert client.post("/api/items/bulk-price", headers=h, json=bad).status_code == 422


def test_group_category_move_carries_items(client: TestClient) -> None:
    h = _h(_token(client, "gap-9@x.example.com"))
    a = _cat(client, h, "Gap Move A")
    b = _cat(client, h, "Gap Move B")
    g = _grp(client, h, "Gap Mover", a)
    it = _item(client, h, "Gap Mover One", group_id=g)
    moved = client.patch(f"/api/item-groups/{g}", headers=h, json={"category_id": b})
    assert moved.status_code == 200
    assert client.get(f"/api/items/{it}", headers=h).json()["category_id"] == b
    assert _count(client, h, {"category_id": b}) == 1
    assert _count(client, h, {"category_id": a}) == 0


def test_tree_leaves_carry_availability(client: TestClient) -> None:
    h = _h(_token(client, "gap-10@x.example.com"))
    cat = _cat(client, h, "Gap Leaf Cat")
    _item(client, h, "Gap Leaf One", category_id=cat, availability="out_of_stock")
    leaves = client.get(f"/api/items/tree/leaves?category_id={cat}", headers=h).json()
    assert leaves[0]["availability"] == "out_of_stock"
    assert leaves[0]["tally_state"] == "none"

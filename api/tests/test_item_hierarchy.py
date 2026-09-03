"""category → group → item: categories seeded on register, group CRUD,
leaf ↔ group inheritance, size ordering, the tree endpoint, resolve_group.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import HsnCode, ProductGroup


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _seed_hsn(session) -> None:  # type: ignore[no-untyped-def]
    for code, desc, ch, rate in (
        ("73239390", "SS household/kitchen articles, other", "73", 12.0),
        ("72142000", "Bars/rods of iron/non-alloy steel, deformed (TMT)", "72", 18.0),
    ):
        if session.scalar(select(HsnCode).where(HsnCode.code == code)) is None:
            session.add(HsnCode(code=code, description=desc, chapter=ch, default_gst_rate=rate))
    session.commit()


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


# --------------------------------------------------------------------------
# categories
# --------------------------------------------------------------------------


def test_categories_seeded_on_register(client: TestClient) -> None:
    h = _h(_token(client, "cat-1@x.example.com"))
    cats = client.get("/api/item-categories", headers=h).json()
    names = {c["name"] for c in cats}
    # the fixed taxonomy: departments + starter brands
    assert {"Cookware", "Pressure Cookers", "Pooja & Wooden Goods"} <= names
    assert {"Hawkins", "Prestige", "Milton"} <= names
    # every taxonomy product_group is seeded too (empty until items land)
    assert all(c["item_count"] == 0 for c in cats)


def test_category_crud(client: TestClient) -> None:
    h = _h(_token(client, "cat-2@x.example.com"))
    made = client.post("/api/item-categories", headers=h, json={"name": "Kanchan"})
    assert made.status_code == 201
    cid = made.json()["id"]

    dupe = client.post("/api/item-categories", headers=h, json={"name": "kanchan"})
    assert dupe.status_code == 409

    ren = client.patch(f"/api/item-categories/{cid}", headers=h, json={"name": "Hawkins Ltd"})
    assert ren.status_code == 200 and ren.json()["name"] == "Hawkins Ltd"

    assert client.request(
        "DELETE", f"/api/item-categories/{cid}", headers=h, json={}
    ).status_code == 204


def test_delete_category_detaches_its_groups(client: TestClient) -> None:
    h = _h(_token(client, "cat-3@x.example.com"))
    cid = client.post("/api/item-categories", headers=h, json={"name": "Temp"}).json()["id"]
    g = client.post(
        "/api/item-groups", headers=h, json={"name": "Temp Group", "category_id": cid}
    ).json()
    assert g["category_id"] == cid

    client.request("DELETE", f"/api/item-categories/{cid}", headers=h, json={})
    after = client.get(f"/api/item-groups/{g['id']}", headers=h).json()
    assert after["category_id"] is None


# --------------------------------------------------------------------------
# groups + leaves + inheritance
# --------------------------------------------------------------------------


def test_group_crud_and_dedupe(client: TestClient) -> None:
    h = _h(_token(client, "grp-1@x.example.com"))
    g = client.post(
        "/api/item-groups",
        headers=h,
        json={
            "name": "MS TMT Bar",
            "hsn_code": "72142000",
            "uom": "kg",
            "item_type": "bulk",
            "default_rate_mode": "kg",
        },
    )
    assert g.status_code == 201
    assert g.json()["name_normalized"]
    assert g.json()["default_rate_mode"] == "kg"

    dupe = client.post("/api/item-groups", headers=h, json={"name": "ms  tmt  bar"})
    assert dupe.status_code == 409


def test_leaf_inherits_from_group(client: TestClient) -> None:
    h = _h(_token(client, "grp-2@x.example.com"))
    cid = client.get("/api/item-categories", headers=h).json()[0]["id"]
    g = client.post(
        "/api/item-groups",
        headers=h,
        json={
            "name": "SS Balti",
            "category_id": cid,
            "hsn_code": "73239390",
            "uom": "nos",
            "item_type": "mrp",
            "default_rate_mode": "piece",
        },
    ).json()

    # a leaf with only a name + group + size
    leaf = client.post(
        "/api/items",
        headers=h,
        json={"name": "SS Balti No.3", "group_id": g["id"], "size_label": "No.3"},
    )
    assert leaf.status_code == 201
    body = leaf.json()
    assert body["group_id"] == g["id"]
    assert body["rate_mode"] == "piece"          # from group.default_rate_mode
    assert body["category_id"] == cid            # from group
    assert body["hsn_code"] == "73239390"        # from group
    assert body["uom"] == "nos"                  # from group
    assert body["item_type"] == "mrp"            # from group
    assert float(body["gst_rate"]) == 12.0       # from the HSN

    # an explicit override wins
    leaf2 = client.post(
        "/api/items",
        headers=h,
        json={
            "name": "SS Balti No.5 kg-sold",
            "group_id": g["id"],
            "size_label": "No.5",
            "rate_mode": "kg",
        },
    ).json()
    assert leaf2["rate_mode"] == "kg"


def test_group_detail_lists_leaves_with_generated_name(client: TestClient) -> None:
    h = _h(_token(client, "grp-3@x.example.com"))
    cat = client.post("/api/item-categories", headers=h, json={"name": "Mintage"}).json()["id"]
    g = client.post(
        "/api/item-groups",
        headers=h,
        json={"name": "Mintage Casserole", "category_id": cat, "default_rate_mode": "piece"},
    ).json()
    client.post(
        "/api/items",
        headers=h,
        json={
            "name": "Mintage 3499 5L", "group_id": g["id"],
            "size_label": "5 Litre", "sku": "3499",
        },
    )
    client.post(
        "/api/items",
        headers=h,
        json={
            "name": "Mintage 5949 15L", "group_id": g["id"],
            "size_label": "15 Litre", "sku": "5949",
        },
    )
    detail = client.get(f"/api/item-groups/{g['id']}", headers=h).json()
    names = {leaf["generated_name"] for leaf in detail["leaves"]}
    assert names == {"Mintage 3499 5 Litre", "Mintage 5949 15 Litre"}


def test_size_reorder(client: TestClient) -> None:
    h = _h(_token(client, "grp-4@x.example.com"))
    g = client.post("/api/item-groups", headers=h, json={"name": "Hawkins Cooker"}).json()
    ids = []
    for label in ("10L", "5L", "3L"):
        it = client.post(
            "/api/items",
            headers=h,
            json={"name": f"Hawkins Cooker {label}", "group_id": g["id"], "size_label": label},
        ).json()
        ids.append(it["id"])
    # order them 3L, 5L, 10L
    ordered = [ids[2], ids[1], ids[0]]
    r = client.patch(
        f"/api/item-groups/{g['id']}/size-order", headers=h, json={"leaf_ids": ordered}
    )
    assert r.status_code == 200
    got = [leaf["size_label"] for leaf in r.json()["leaves"]]
    assert got == ["3L", "5L", "10L"]


def test_delete_group_detaches_leaves(client: TestClient) -> None:
    h = _h(_token(client, "grp-5@x.example.com"))
    g = client.post("/api/item-groups", headers=h, json={"name": "Doomed Group"}).json()
    it = client.post(
        "/api/items",
        headers=h,
        json={"name": "Doomed Group Item A", "group_id": g["id"]},
    ).json()
    assert client.delete(f"/api/item-groups/{g['id']}", headers=h).status_code == 204
    after = client.get(f"/api/items/{it['id']}", headers=h).json()
    assert after["group_id"] is None


# --------------------------------------------------------------------------
# tree
# --------------------------------------------------------------------------


def test_tree_shape(client: TestClient) -> None:
    h = _h(_token(client, "tree-1@x.example.com"))
    # a fresh category so the group list is exactly what this test creates
    steel = client.post(
        "/api/item-categories", headers=h, json={"name": "MS Bar Stock"}
    ).json()["id"]
    g = client.post(
        "/api/item-groups", headers=h, json={"name": "MS Angle", "category_id": steel}
    ).json()
    client.post(
        "/api/items",
        headers=h,
        json={"name": "MS Angle 40x40x5", "group_id": g["id"], "size_label": "40x40x5"},
    )
    # a loose leaf in the category with no group (category_id set -> the
    # create-time classifier is skipped, so it stays loose)
    client.post(
        "/api/items",
        headers=h,
        json={"name": "MS Scrap Mixed", "category_id": steel},
    )

    # /tree is now the skeleton: groups + counts, no leaf rows
    tree = client.get("/api/items/tree", headers=h).json()
    steel_node = next(c for c in tree if c["name"] == "MS Bar Stock")
    assert [g["name"] for g in steel_node["groups"]] == ["MS Angle"]
    assert steel_node["groups"][0]["leaf_count"] == 1
    assert steel_node["loose_count"] == 1

    # leaves come from /tree/leaves per node
    grp_id = steel_node["groups"][0]["id"]
    grp_leaves = client.get(
        f"/api/items/tree/leaves?group_id={grp_id}", headers=h
    ).json()
    assert [x["name"] for x in grp_leaves] == ["MS Angle 40x40x5"]
    loose = client.get(
        f"/api/items/tree/leaves?category_id={steel}", headers=h
    ).json()
    assert [x["name"] for x in loose] == ["MS Scrap Mixed"]

    # bad selector combos are rejected
    assert client.get("/api/items/tree/leaves", headers=h).status_code == 422
    assert (
        client.get(
            f"/api/items/tree/leaves?group_id={grp_id}&uncategorised=true", headers=h
        ).status_code
        == 422
    )


# --------------------------------------------------------------------------
# resolve_group
# --------------------------------------------------------------------------


def test_resolve_group_exact_and_none(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    from app.services.item_resolution import resolve_group

    h = _h(_token(client, "rg-1@x.example.com"))
    g = client.post("/api/item-groups", headers=h, json={"name": "ST Storage Box"}).json()

    grp = session.scalar(select(ProductGroup).where(ProductGroup.id == g["id"]))
    m = resolve_group(session, grp.tenant_id, "st storage box")
    assert m.group_id == g["id"] and m.method.value == "exact"

    miss = resolve_group(session, grp.tenant_id, "something entirely different")
    assert miss.group_id is None

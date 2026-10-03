"""Product registry: supplier at upload, reuse across PDFs, suggestions, group codes, locking."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.catalog.conftest import SAMPLE_PDF
from tests.catalog.pdfs import TestCell, make_pdf
from tests.catalog.test_api_import import CatalogEnv, _items, _upload


def _party(client: TestClient, h: dict[str, str], name: str, role: str = "supplier") -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": name, "role": role})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _cells(*codes: str, name: str = "DELI 190 ML JUICE GLASS") -> bytes:
    return make_pdf([[TestCell(code=c, name_lines=[name, "COL BOX 6 PC"]) for c in codes]])


def _by_supplier_code(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["supplier_code"]: i for i in items}


def _groups(client: TestClient, h: dict[str, str], cid: str) -> list[dict[str, Any]]:
    r = client.get(f"/api/supplier-catalogs/{cid}/groups", headers=h)
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


# --- supplier at upload --------------------------------------------------------


def test_upload_with_supplier_records_it(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    r = _upload(client, h, supplier_party_id=sup)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["supplier_party_id"] == sup and body["supplier_name"] == "Sugal Glass House"
    assert body["new_products"] == 24 and body["matched_items"] == 0
    listed = client.get("/api/supplier-catalogs", headers=h).json()
    assert listed[0]["supplier_name"] == "Sugal Glass House"
    detail = client.get(f"/api/supplier-catalogs/{body['id']}", headers=h).json()
    assert detail["supplier_name"] == "Sugal Glass House"


def test_supplier_must_exist_and_be_a_supplier(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cust = _party(client, h, "Retail Customer", role="customer")
    assert _upload(client, h, supplier_party_id=cust).status_code == 422
    assert _upload(client, h, supplier_party_id="no-such-id").status_code == 422
    both = _party(client, h, "Both Ways Traders", role="both")
    assert _upload(client, h, supplier_party_id=both).status_code == 201


def test_another_firms_party_is_not_a_valid_supplier(
    catalog_client: CatalogEnv, client: TestClient
) -> None:
    from tests.catalog.conftest import auth, register

    c, h, _ = catalog_client
    other = auth(register(client, "other@catalog.example.com"))
    foreign = _party(client, other, "Foreign Supplier")
    assert _upload(c, h, supplier_party_id=foreign).status_code == 422


# --- reuse across PDFs -----------------------------------------------------------


def test_same_supplier_same_code_reuses_the_product(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    a = _upload(client, h, data=_cells("Z1", "Z2"), name="a.pdf", supplier_party_id=sup).json()
    first = _by_supplier_code(_items(client, h, a["id"]))

    b = _upload(client, h, data=_cells("Z2", "Z3"), name="b.pdf", supplier_party_id=sup).json()
    assert b["matched_items"] == 1 and b["new_products"] == 1
    second = _by_supplier_code(_items(client, h, b["id"]))
    assert second["Z2"]["code"] == first["Z2"]["code"]
    assert second["Z2"]["product_id"] == first["Z2"]["product_id"]
    assert second["Z3"]["code"] not in {first["Z1"]["code"], first["Z2"]["code"]}


def test_the_same_supplier_code_from_another_supplier_is_a_different_product(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    s1 = _party(client, h, "Supplier One")
    s2 = _party(client, h, "Supplier Two")
    a = _upload(client, h, data=_cells("Z1"), name="a.pdf", supplier_party_id=s1).json()
    b = _upload(client, h, data=_cells("Z1"), name="b.pdf", supplier_party_id=s2).json()
    assert b["matched_items"] == 0
    ia, ib = _items(client, h, a["id"])[0], _items(client, h, b["id"])[0]
    assert ia["code"] != ib["code"] and ia["product_id"] != ib["product_id"]


def test_without_a_supplier_nothing_is_matched(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    b = _upload(client, h, data=_cells("Z1"), name="b.pdf").json()
    assert b["matched_items"] == 0
    assert _items(client, h, a["id"])[0]["code"] != _items(client, h, b["id"])[0]["code"]


def test_edits_are_remembered_for_the_next_pdf(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    a = _upload(client, h, data=_cells("Z1", "Z2"), name="a.pdf", supplier_party_id=sup).json()
    item = _by_supplier_code(_items(client, h, a["id"]))["Z1"]
    r = client.patch(
        f"/api/supplier-catalogs/{a['id']}/items/{item['id']}",
        headers=h,
        json={"display_name": "Juice Glass 190 ML", "group_name": "Tumblers"},
    )
    assert r.status_code == 200, r.text
    edited = r.json()

    b = _upload(client, h, data=_cells("Z1", "Z9"), name="b.pdf", supplier_party_id=sup).json()
    again = _by_supplier_code(_items(client, h, b["id"]))["Z1"]
    assert again["display_name"] == "Juice Glass 190 ML"
    assert again["category_name"] == "Tumblers"
    assert again["code"] == edited["code"]


# --- group codes -----------------------------------------------------------------


def test_group_gets_a_code_and_items_use_it(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h).json()
    groups = _groups(client, h, cat["id"])
    mugs = next(g for g in groups if g["name"] == "Beer Mugs")
    assert mugs["code_prefix"] == "BM"
    cats = client.get("/api/item-categories", headers=h).json()
    assert next(c for c in cats if c["name"] == "Beer Mugs")["code_prefix"] == "BM"


def test_regrouping_a_provisional_item_reissues_its_code(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1", "Z2"), name="a.pdf").json()
    item = _items(client, h, cat["id"])[0]
    old = item["code"]
    r = client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/{item['id']}",
        headers=h,
        json={"group_name": "Beer Mugs"},
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["category_name"] == "Beer Mugs"
    assert new["code"].startswith("BM-") and new["code"] != old
    # the product (and so the next catalog) carries the new code
    assert _items(client, h, cat["id"])[0]["code"] == new["code"]


def test_regrouping_to_the_same_group_keeps_the_code(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    item = _items(client, h, cat["id"])[0]
    url = f"/api/supplier-catalogs/{cat['id']}/items/{item['id']}"
    first = client.patch(url, headers=h, json={"group_name": "Beer Mugs"}).json()
    second = client.patch(url, headers=h, json={"group_name": "Beer Mugs"}).json()
    assert first["code"] == second["code"]


def test_bulk_group_change_reissues_provisional_codes(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1", "Z2", "Z3"), name="a.pdf").json()
    items = _items(client, h, cat["id"])
    r = client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/bulk",
        headers=h,
        json={"ids": [i["id"] for i in items], "changes": {"group_name": "Beer Mugs"}},
    )
    assert r.status_code == 200 and r.json()["updated"] == 3
    after = _items(client, h, cat["id"])
    assert [i["code"] for i in after] == ["BM-0001", "BM-0002", "BM-0003"]
    assert {i["category_name"] for i in after} == {"Beer Mugs"}


def test_labels_lock_the_code_and_regroup_then_keeps_it(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1", "Z2"), name="a.pdf").json()
    items = _items(client, h, cat["id"])
    locked, free = items[0], items[1]
    r = client.post(
        f"/api/supplier-catalogs/{cat['id']}/labels",
        headers=h,
        json={"ids": [locked["id"]], "preset": "roll_50x25"},
    )
    assert r.status_code == 200, r.text
    after = _by_supplier_code(_items(client, h, cat["id"]))
    assert after[locked["supplier_code"]]["code_locked"] is True
    assert after[free["supplier_code"]]["code_locked"] is False

    moved = client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/{locked['id']}",
        headers=h,
        json={"group_name": "Beer Mugs"},
    ).json()
    assert moved["category_name"] == "Beer Mugs"
    assert moved["code"] == locked["code"]  # printed on a label: never changes


def test_customer_catalog_with_codes_locks_them(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    r = client.post(
        f"/api/supplier-catalogs/{cat['id']}/customer-catalogs",
        headers=h,
        json={"all_included": True, "show_code": True},
    )
    assert r.status_code == 201, r.text
    assert _items(client, h, cat["id"])[0]["code_locked"] is True


def test_customer_catalog_without_codes_does_not_lock(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    r = client.post(
        f"/api/supplier-catalogs/{cat['id']}/customer-catalogs",
        headers=h,
        json={"all_included": True, "show_code": False},
    )
    assert r.status_code == 201, r.text
    assert _items(client, h, cat["id"])[0]["code_locked"] is False


def test_group_code_can_be_changed_until_a_code_uses_it(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    item = _items(client, h, cat["id"])[0]
    client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/{item['id']}",
        headers=h,
        json={"group_name": "Beer Mugs"},
    )
    bm = next(g for g in _groups(client, h, cat["id"]) if g["name"] == "Beer Mugs")
    # a code BM-0001 exists, so BM is fixed
    r = client.patch(
        f"/api/item-categories/{bm['category_id']}", headers=h, json={"code_prefix": "BX"}
    )
    assert r.status_code == 409
    # a fresh group with no codes yet can be renamed to any free code
    c = client.post("/api/item-categories", headers=h, json={"name": "Spoons"}).json()
    ok = client.patch(f"/api/item-categories/{c['id']}", headers=h, json={"code_prefix": "sp"})
    assert ok.status_code == 200 and ok.json()["code_prefix"] == "SP"


def test_group_code_validation_and_uniqueness(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a = client.post("/api/item-categories", headers=h, json={"name": "Spoons"}).json()
    b = client.post("/api/item-categories", headers=h, json={"name": "Forks"}).json()
    assert (
        client.patch(
            f"/api/item-categories/{a['id']}", headers=h, json={"code_prefix": "S"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/item-categories/{a['id']}", headers=h, json={"code_prefix": "S-P"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/item-categories/{a['id']}", headers=h, json={"code_prefix": "SP"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/item-categories/{b['id']}", headers=h, json={"code_prefix": "SP"}
        ).status_code
        == 409
    )
    # the firm's ungrouped prefix is reserved
    assert (
        client.patch(
            f"/api/item-categories/{b['id']}", headers=h, json={"code_prefix": "GEN"}
        ).status_code
        == 409
    )


def test_merging_groups_moves_the_products(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    item = _items(client, h, cat["id"])[0]
    client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/{item['id']}",
        headers=h,
        json={"group_name": "Beer Mugs"},
    )
    other = client.post("/api/item-categories", headers=h, json={"name": "Mugs"}).json()
    src = next(g for g in _groups(client, h, cat["id"]) if g["name"] == "Beer Mugs")["category_id"]
    r = client.post(f"/api/item-categories/{src}/merge", headers=h, json={"into": other["id"]})
    assert r.status_code == 200, r.text
    moved = _items(client, h, cat["id"])[0]
    assert moved["category_name"] == "Mugs"
    # a re-import of the same product still finds it in the merged group
    assert moved["code"].startswith("BM-")  # the code itself is not rewritten


# --- suggestions -----------------------------------------------------------------


def _two_suppliers(client: TestClient, h: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    s1 = _party(client, h, "Supplier One")
    s2 = _party(client, h, "Supplier Two")
    a = _upload(client, h, data=_cells("A1"), name="a.pdf", supplier_party_id=s1).json()
    b = _upload(client, h, data=_cells("B1"), name="b.pdf", supplier_party_id=s2).json()
    return a, b


def test_same_name_from_another_supplier_is_suggested(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a, b = _two_suppliers(client, h)
    assert b["suggestions"] == 1
    mine = _items(client, h, b["id"])[0]
    theirs = _items(client, h, a["id"])[0]
    assert mine["suggestion"]["code"] == theirs["code"]
    assert mine["suggestion"]["product_id"] == theirs["product_id"]
    assert _items(client, h, a["id"])[0]["suggestion"] is None  # first one has nothing to match


def test_accepting_a_suggestion_adopts_the_other_products_code(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a, b = _two_suppliers(client, h)
    mine = _items(client, h, b["id"])[0]
    theirs = _items(client, h, a["id"])[0]
    r = client.post(
        f"/api/supplier-catalogs/{b['id']}/items/{mine['id']}/link-product",
        headers=h,
        json={"product_id": mine["suggestion"]["product_id"]},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["code"] == theirs["code"] and out["product_id"] == theirs["product_id"]
    assert out["suggestion"] is None
    assert _items(client, h, b["id"])[0]["code"] == theirs["code"]


def test_dismissing_a_suggestion_clears_it(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    _, b = _two_suppliers(client, h)
    mine = _items(client, h, b["id"])[0]
    r = client.delete(f"/api/supplier-catalogs/{b['id']}/items/{mine['id']}/suggestion", headers=h)
    assert r.status_code == 200 and r.json()["suggestion"] is None
    assert r.json()["code"] == mine["code"]  # unchanged
    assert _items(client, h, b["id"])[0]["suggestion"] is None


def test_cannot_accept_a_suggestion_once_the_code_is_locked(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    _, b = _two_suppliers(client, h)
    mine = _items(client, h, b["id"])[0]
    client.post(
        f"/api/supplier-catalogs/{b['id']}/labels",
        headers=h,
        json={"ids": [mine["id"]], "preset": "roll_50x25"},
    )
    r = client.post(
        f"/api/supplier-catalogs/{b['id']}/items/{mine['id']}/link-product",
        headers=h,
        json={"product_id": mine["suggestion"]["product_id"]},
    )
    assert r.status_code == 409


def test_link_to_unknown_product_is_404(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    _, b = _two_suppliers(client, h)
    mine = _items(client, h, b["id"])[0]
    r = client.post(
        f"/api/supplier-catalogs/{b['id']}/items/{mine['id']}/link-product",
        headers=h,
        json={"product_id": "nope"},
    )
    assert r.status_code == 404


# --- supplier added later --------------------------------------------------------


def test_supplier_can_be_attached_afterwards_and_then_matches(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Late Supplier")
    a = _upload(client, h, data=_cells("Z1"), name="a.pdf").json()
    r = client.patch(
        f"/api/supplier-catalogs/{a['id']}", headers=h, json={"supplier_party_id": sup}
    )
    assert r.status_code == 200, r.text
    assert r.json()["supplier_name"] == "Late Supplier"
    b = _upload(client, h, data=_cells("Z1"), name="b.pdf", supplier_party_id=sup).json()
    assert b["matched_items"] == 1
    assert _items(client, h, b["id"])[0]["code"] == _items(client, h, a["id"])[0]["code"]


def test_supplier_cannot_be_changed_once_set(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    s1 = _party(client, h, "Supplier One")
    s2 = _party(client, h, "Supplier Two")
    a = _upload(client, h, data=_cells("Z1"), name="a.pdf", supplier_party_id=s1).json()
    r = client.patch(f"/api/supplier-catalogs/{a['id']}", headers=h, json={"supplier_party_id": s2})
    assert r.status_code == 409


def test_attaching_a_supplier_that_already_has_those_codes_is_refused(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Supplier One")
    _upload(client, h, data=_cells("Z1"), name="a.pdf", supplier_party_id=sup)
    loose = _upload(client, h, data=_cells("Z1", "Z5"), name="b.pdf").json()
    r = client.patch(
        f"/api/supplier-catalogs/{loose['id']}", headers=h, json={"supplier_party_id": sup}
    )
    assert r.status_code == 409 and "Z1" in r.json()["detail"]


# --- deleting a catalog keeps the products ---------------------------------------


def test_deleting_a_catalog_leaves_the_products_so_a_reimport_matches(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    a = _upload(client, h, data=_cells("Z1"), name="a.pdf", supplier_party_id=sup).json()
    code = _items(client, h, a["id"])[0]["code"]
    assert client.delete(f"/api/supplier-catalogs/{a['id']}", headers=h).status_code == 204
    b = _upload(client, h, data=_cells("Z1"), name="a2.pdf", supplier_party_id=sup).json()
    assert b["matched_items"] == 1
    assert _items(client, h, b["id"])[0]["code"] == code


def test_sample_pdf_with_supplier_then_reupload_of_a_variant_matches_everything(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    a = _upload(client, h, supplier_party_id=sup, name="one.pdf").json()
    # same products, different file bytes (a trailing comment keeps it a valid PDF)
    variant = SAMPLE_PDF.read_bytes() + b"\n% variant\n"
    b = _upload(client, h, data=variant, name="two.pdf", supplier_party_id=sup).json()
    assert b["already_exists"] is False
    assert b["matched_items"] == 24 and b["new_products"] == 0
    assert [i["code"] for i in _items(client, h, a["id"], limit=200)] == [
        i["code"] for i in _items(client, h, b["id"], limit=200)
    ]

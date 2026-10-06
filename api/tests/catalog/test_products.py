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


# --- group changes never touch codes --------------------------------------------------


def test_groups_are_created_and_codes_carry_no_group(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h).json()
    groups = _groups(client, h, cat["id"])
    assert "Beer Mugs" in [g["name"] for g in groups]
    assert all("code_prefix" not in g for g in groups)
    cats = client.get("/api/item-categories", headers=h).json()
    assert all("code_prefix" not in c for c in cats)


def test_regrouping_an_item_keeps_its_code(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1", "Z2"), name="a.pdf").json()
    item = _items(client, h, cat["id"])[0]
    r = client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/{item['id']}",
        headers=h,
        json={"group_name": "Beer Mugs"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["category_name"] == "Beer Mugs" and r.json()["code"] == item["code"]
    assert _items(client, h, cat["id"])[0]["code"] == item["code"]


def test_bulk_regroup_keeps_codes_and_follows_the_product(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("Z1", "Z2", "Z3"), name="a.pdf").json()
    before = _items(client, h, cat["id"])
    r = client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/bulk",
        headers=h,
        json={"ids": [i["id"] for i in before], "changes": {"group_name": "Beer Mugs"}},
    )
    assert r.status_code == 200 and r.json()["updated"] == 3
    after = _items(client, h, cat["id"])
    assert [i["code"] for i in after] == [i["code"] for i in before]
    assert {i["category_name"] for i in after} == {"Beer Mugs"}


def test_a_regrouped_product_keeps_its_group_on_the_next_pdf(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    a = _upload(client, h, data=_cells("Z1"), name="a.pdf", supplier_party_id=sup).json()
    item = _items(client, h, a["id"])[0]
    client.patch(
        f"/api/supplier-catalogs/{a['id']}/items/{item['id']}",
        headers=h,
        json={"group_name": "Tumblers"},
    )
    b = _upload(client, h, data=_cells("Z1", "Z9"), name="b.pdf", supplier_party_id=sup).json()
    again = _by_supplier_code(_items(client, h, b["id"]))["Z1"]
    assert again["category_name"] == "Tumblers" and again["code"] == item["code"]


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
    assert moved["category_name"] == "Mugs" and moved["code"] == item["code"]


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


def test_cannot_accept_a_suggestion_once_the_row_is_in_the_item_list(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    _, b = _two_suppliers(client, h)
    mine = _items(client, h, b["id"])[0]
    client.post(f"/api/supplier-catalogs/{b['id']}/promote", headers=h, json={"ids": [mine["id"]]})
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


# --- suppliers are parties: guards and the per-supplier summary -------------------------


def test_supplier_summary_lists_catalogs_and_product_counts(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    other = _party(client, h, "Other Supplier")
    empty = client.get(f"/api/supplier-catalogs/by-supplier/{sup}", headers=h).json()
    assert empty == {"catalogs": [], "product_count": 0, "promoted_count": 0}

    a = _upload(client, h, data=_cells("Z1", "Z2"), name="a.pdf", supplier_party_id=sup).json()
    _upload(client, h, data=_cells("Y1"), name="b.pdf", supplier_party_id=other)
    client.post(
        f"/api/supplier-catalogs/{a['id']}/promote",
        headers=h,
        json={"ids": [_items(client, h, a["id"])[0]["id"]]},
    )
    out = client.get(f"/api/supplier-catalogs/by-supplier/{sup}", headers=h).json()
    assert [c["id"] for c in out["catalogs"]] == [a["id"]]
    assert out["catalogs"][0]["supplier_name"] == "Sugal Glass House"
    assert out["product_count"] == 2 and out["promoted_count"] == 1


def test_supplier_summary_is_tenant_scoped(catalog_client: CatalogEnv, client: TestClient) -> None:
    from tests.catalog.conftest import auth, register

    c, h, _ = catalog_client
    other = auth(register(client, "other2@catalog.example.com"))
    foreign = _party(client, other, "Foreign Supplier")
    assert c.get(f"/api/supplier-catalogs/by-supplier/{foreign}", headers=h).status_code == 404


def test_a_supplier_with_catalogs_cannot_be_deleted_or_made_customer_only(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    _upload(client, h, data=_cells("Z1"), name="a.pdf", supplier_party_id=sup)
    r = client.delete(f"/api/parties/{sup}", headers=h)
    assert r.status_code == 409 and "catalogs" in r.json()["detail"]
    r = client.patch(f"/api/parties/{sup}", headers=h, json={"role": "customer"})
    assert r.status_code == 409
    assert client.patch(f"/api/parties/{sup}", headers=h, json={"role": "both"}).status_code == 200
    assert (
        client.patch(f"/api/parties/{sup}", headers=h, json={"status": "archived"}).status_code
        == 200
    )


def test_a_supplier_without_catalogs_can_still_be_deleted(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Unused Supplier")
    assert client.delete(f"/api/parties/{sup}", headers=h).status_code == 204


def test_an_archived_supplier_cannot_receive_a_new_catalog(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    sup = _party(client, h, "Sugal Glass House")
    client.patch(f"/api/parties/{sup}", headers=h, json={"status": "archived"})
    r = _upload(client, h, supplier_party_id=sup)
    assert r.status_code == 422 and "archived" in r.json()["detail"]


def test_possible_matches_filter_count_and_supplier_name(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a, b = _two_suppliers(client, h)
    detail = client.get(f"/api/supplier-catalogs/{b['id']}", headers=h).json()
    assert detail["suggestion_count"] == 1
    assert (
        client.get(f"/api/supplier-catalogs/{a['id']}", headers=h).json()["suggestion_count"] == 0
    )

    only = _items(client, h, b["id"], has_suggestion="true")
    assert len(only) == 1 and only[0]["suggestion"]["supplier_name"] == "Supplier One"
    assert _items(client, h, a["id"], has_suggestion="true") == []

    # dismissing removes it from the filter and the count
    client.delete(f"/api/supplier-catalogs/{b['id']}/items/{only[0]['id']}/suggestion", headers=h)
    assert _items(client, h, b["id"], has_suggestion="true") == []
    assert (
        client.get(f"/api/supplier-catalogs/{b['id']}", headers=h).json()["suggestion_count"] == 0
    )


def test_accept_and_dismiss_matches_in_bulk(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    s1 = _party(client, h, "Supplier One")
    s2 = _party(client, h, "Supplier Two")
    a = _upload(client, h, data=_cells("A1", "A2"), name="a.pdf", supplier_party_id=s1).json()
    # same names again from another supplier: every row has a possible match
    b = _upload(client, h, data=_cells("B1", "B2"), name="b.pdf", supplier_party_id=s2).json()
    url = f"/api/supplier-catalogs/{b['id']}/matches"
    assert b["suggestions"] >= 1
    rows = _items(client, h, b["id"], has_suggestion=True)
    # dismiss one row only
    r = client.post(f"{url}/dismiss", headers=h, json={"ids": [rows[0]["id"]]})
    assert r.json() == {"done": 1, "skipped": 0}
    left = _items(client, h, b["id"], has_suggestion=True)
    assert len(left) == len(rows) - 1
    # accept everything still suggested, by filter
    r = client.post(f"{url}/accept", headers=h, json={"filter": {"has_suggestion": True}})
    assert r.status_code == 200 and r.json()["done"] == len(left)
    assert _items(client, h, b["id"], has_suggestion=True) == []
    codes_a = {i["code"] for i in _items(client, h, a["id"])}
    taken = [i for i in _items(client, h, b["id"]) if i["code"] in codes_a]
    assert len(taken) == len(left)
    # nothing left to do: a no-op, not an error
    assert client.post(f"{url}/accept", headers=h, json={"all_included": True}).json() == {
        "done": 0,
        "skipped": 0,
    }

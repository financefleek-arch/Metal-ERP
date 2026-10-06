"""Items hub foundation: availability, pack size, filters, selection by filter, came from,
and the delete / merge links to price lists."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import CatalogProduct, InwardBill, InwardBillLine, Item, SupplierCatalogItem
from tests.catalog.pdfs import TestCell, make_pdf
from tests.catalog.test_api_import import CatalogEnv, _items, _upload
from tests.catalog.test_products import _party


def _cells(*codes: str) -> bytes:
    return make_pdf(
        [
            [
                TestCell(
                    code=c,
                    price_line="Rs 190 for 6 pcs IN STOCK",
                    name_lines=[f"BEER MUG {c} ML", "COL BOX 6 PC COL BOX 12 SET CTN"],
                )
                for c in codes
            ]
        ]
    )


def _promote(client: TestClient, h: dict[str, str], cid: str, **sel: Any) -> dict:
    r = client.post(
        f"/api/supplier-catalogs/{cid}/promote", headers=h, json=sel or {"all_included": True}
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _list(client: TestClient, h: dict[str, str], **params: Any) -> list[dict]:
    r = client.get("/api/items", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _env(catalog_client: CatalogEnv, codes=("11", "12", "13")) -> tuple[Any, dict, str, str]:
    client, h, _ = catalog_client
    sup = _party(client, h, "Yujing Glass Traders")
    cat = _upload(client, h, data=_cells(*codes), name="a.pdf", supplier_party_id=sup).json()
    _promote(client, h, cat["id"])
    return client, h, sup, cat["id"]


# --- availability and pack size ----------------------------------------------------------------


def test_hand_added_items_are_in_stock_and_can_be_changed(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    made = client.post("/api/items", headers=h, json={"name": "Steel Plate", "uom": "nos"}).json()
    assert made["availability"] == "in_stock" and made["pack_qty"] is None
    r = client.patch(
        f"/api/items/{made['id']}",
        headers=h,
        json={"availability": "expected", "pack_qty": 12, "carton_qty": 72},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["availability"], r.json()["pack_qty"], r.json()["carton_qty"]) == (
        "expected",
        12,
        72,
    )
    bad = client.patch(f"/api/items/{made['id']}", headers=h, json={"availability": "gone"})
    assert bad.status_code == 422
    zero = client.patch(f"/api/items/{made['id']}", headers=h, json={"pack_qty": 0})
    assert zero.status_code == 422


def test_price_list_items_start_out_of_stock_with_pack_size(catalog_client: CatalogEnv) -> None:
    client, h, _, _ = _env(catalog_client)
    items = _list(client, h)
    assert len(items) == 3
    assert {i["availability"] for i in items} == {"out_of_stock"}
    assert {i["pack_qty"] for i in items} == {6}
    assert {i["carton_qty"] for i in items} == {12}


def test_linking_to_an_existing_item_keeps_its_availability(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("21"), name="a.pdf").json()
    row = _items(client, h, cat["id"])[0]
    mine = client.post("/api/items", headers=h, json={"name": row["display_name"]}).json()
    assert _promote(client, h, cat["id"])["link_existing"] == 1
    after = client.get(f"/api/items/{mine['id']}", headers=h).json()
    assert after["availability"] == "in_stock"  # the shop's own item is not demoted
    assert after["pack_qty"] == 6  # but it learns its pack size


# --- filters -----------------------------------------------------------------------------------


def test_filters_availability_photo_tally_supplier_and_catalog(catalog_client: CatalogEnv) -> None:
    client, h, sup, cid = _env(catalog_client)
    other = client.post("/api/items", headers=h, json={"name": "Hand made"}).json()
    ids = {i["name"]: i["id"] for i in _list(client, h)}
    assert len(ids) == 4

    assert len(_list(client, h, availability="out_of_stock")) == 3
    assert [i["name"] for i in _list(client, h, availability="in_stock")] == ["Hand made"]
    assert len(_list(client, h, availability=["in_stock", "out_of_stock"])) == 4
    assert {i["name"] for i in _list(client, h, supplier_id=sup)} == set(ids) - {"Hand made"}
    assert len(_list(client, h, catalog_id=cid)) == 3
    assert _list(client, h, catalog_id="nope") == []

    with SessionLocal() as s:
        s.get(Item, other["id"]).tally_guid = "g-1"
        s.commit()
    assert [i["id"] for i in _list(client, h, in_tally="true")] == [other["id"]]
    assert len(_list(client, h, in_tally="false")) == 3

    # photos copied from the catalog: promote ran, so catalog items have one; hand-made none
    assert [i["id"] for i in _list(client, h, no_photo="true")] == [other["id"]]


# --- selection by filter -----------------------------------------------------------------------


def test_count_matches_the_filter(catalog_client: CatalogEnv) -> None:
    client, h, sup, _ = _env(catalog_client)
    c = client.post("/api/items/count", headers=h, json={"supplier_id": sup})
    assert c.json() == {"count": 3}
    none = client.post("/api/items/count", headers=h, json={"availability": ["in_stock"]})
    assert none.json() == {"count": 0}


def test_bulk_mark_in_stock_by_filter_with_a_preview(catalog_client: CatalogEnv) -> None:
    client, h, sup, _ = _env(catalog_client)
    body = {
        "filter": {"supplier_id": sup, "availability": ["out_of_stock"]},
        "fields": {"availability": "in_stock"},
        "fields_set": ["availability"],
    }
    dry = client.patch("/api/items/bulk?dry_run=true", headers=h, json=body).json()
    assert dry["dry_run"] is True and dry["changed"] == 3
    assert {i["availability"] for i in _list(client, h)} == {"out_of_stock"}  # nothing written

    done = client.patch("/api/items/bulk", headers=h, json=body).json()
    assert done["changed"] == 3 and done["errors"] == 0
    assert {i["availability"] for i in _list(client, h)} == {"in_stock"}
    wide = {**body, "filter": {"supplier_id": sup}}
    again = client.patch("/api/items/bulk", headers=h, json=wide).json()
    assert again["changed"] == 0 and again["unchanged"] == 3


def test_bulk_needs_ids_or_a_filter_not_both(catalog_client: CatalogEnv) -> None:
    client, h, sup, _ = _env(catalog_client)
    fields = {"fields": {"availability": "in_stock"}, "fields_set": ["availability"]}
    both = {"ids": ["x"], "filter": {"supplier_id": sup}, **fields}
    assert client.patch("/api/items/bulk", headers=h, json=both).status_code == 422
    assert client.patch("/api/items/bulk", headers=h, json=fields).status_code == 422


def test_a_runaway_filter_is_refused(
    catalog_client: CatalogEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.item_filter as items_router

    client, h, _, _ = _env(catalog_client)
    monkeypatch.setattr(items_router, "MAX_BULK_FILTER", 2)
    r = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"filter": {}, "fields": {"availability": "in_stock"}, "fields_set": ["availability"]},
    )
    assert r.status_code == 422 and "Narrow it" in r.json()["detail"]


def test_bulk_pack_size_by_filter(catalog_client: CatalogEnv) -> None:
    client, h, sup, _ = _env(catalog_client)
    r = client.patch(
        "/api/items/bulk",
        headers=h,
        json={
            "filter": {"supplier_id": sup},
            "fields": {"pack_qty": 8},
            "fields_set": ["pack_qty"],
        },
    )
    assert r.json()["changed"] == 3
    assert {i["pack_qty"] for i in _list(client, h)} == {8}


# --- delete and merge keep the price-list links sound ------------------------------------------


def test_deleting_a_promoted_item_clears_the_price_list_links(catalog_client: CatalogEnv) -> None:
    client, h, _, cid = _env(catalog_client)
    victim = _items(client, h, cid)[0]
    assert client.delete(f"/api/items/{victim['item_id']}", headers=h).status_code == 204
    after = {r["id"]: r for r in _items(client, h, cid)}
    assert after[victim["id"]]["item_id"] is None
    with SessionLocal() as s:
        prod = s.get(CatalogProduct, victim["product_id"])
        assert prod is not None and prod.item_id is None
    # the row can be added again, and gets a fresh item
    assert _promote(client, h, cid, ids=[victim["id"]])["create"] == 1


def test_bulk_delete_by_filter_clears_links_too(catalog_client: CatalogEnv) -> None:
    client, h, sup, cid = _env(catalog_client)
    r = client.post("/api/items/bulk-delete", headers=h, json={"filter": {"supplier_id": sup}})
    assert r.status_code == 200 and r.json()["deleted"] == 3
    assert all(row["item_id"] is None for row in _items(client, h, cid))
    with SessionLocal() as s:
        linked = s.query(SupplierCatalogItem).filter(SupplierCatalogItem.item_id.is_not(None))
        assert linked.count() == 0


def test_merging_repoints_the_price_list_links_and_keeps_the_photo(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _, cid = _env(catalog_client)
    rows = _items(client, h, cid)
    loser = rows[0]
    winner = client.post("/api/items", headers=h, json={"name": "The real one"}).json()
    r = client.post(
        f"/api/items/{loser['item_id']}/merge", headers=h, json={"target_id": winner["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["photo_url"]  # the winner had none, so it takes the loser's photo
    moved = next(x for x in _items(client, h, cid) if x["id"] == loser["id"])
    assert moved["item_id"] == winner["id"]


# --- came from ---------------------------------------------------------------------------------


def test_sources_list_price_lists_and_bills(catalog_client: CatalogEnv) -> None:
    client, h, _, cid = _env(catalog_client, codes=("31",))
    row = _items(client, h, cid)[0]
    item_id = row["item_id"]
    with SessionLocal() as s:
        bill = InwardBill(
            tenant_id=s.get(Item, item_id).tenant_id,
            source_filename="b.pdf",
            supplier_name="Yujing Glass Traders",
            bill_no="INV-9",
            bill_date=date(2026, 10, 2),
        )
        s.add(bill)
        s.flush()
        s.add(
            InwardBillLine(
                inward_bill_id=bill.id,
                sl_no=1,
                description="Beer mug",
                quantity=24,
                uom="nos",
                unit_rate=210,
                matched_item_id=item_id,
            )
        )
        s.commit()
    src = client.get(f"/api/items/{item_id}/sources", headers=h).json()
    assert [(c["catalog_id"], c["supplier_name"], c["supplier_code"]) for c in src["catalogs"]] == [
        (cid, "Yujing Glass Traders", "31")
    ]
    assert src["catalogs"][0]["cost_price"] == "190.00"
    assert [(b["bill_no"], b["rate"], b["quantity"]) for b in src["bills"]] == [
        ("INV-9", "210.00", "24")
    ]


def test_sources_of_another_firms_item_is_not_found(
    catalog_client: CatalogEnv, client: TestClient
) -> None:
    from tests.catalog.conftest import auth, register

    c, h, _ = catalog_client
    other = auth(register(client, "other5@catalog.example.com"))
    foreign = client.post("/api/items", headers=other, json={"name": "Theirs"}).json()
    assert c.get(f"/api/items/{foreign['id']}/sources", headers=h).status_code == 404

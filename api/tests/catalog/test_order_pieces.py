"""Customer ordering: loose pieces, chosen per catalog link, priced as pack / pieces (+ premium)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CustomerOrderLine
from app.routers import orders as orders_router
from app.services.piece_price import piece_rate
from tests.catalog.test_api_import import CatalogEnv
from tests.catalog.test_customer_orders import LINKS, _item


def _link(client: TestClient, h: dict[str, str], items: list[dict], **extra: Any) -> dict:
    r = client.post(
        LINKS,
        headers=h,
        json={"title": "Range", "selection": {"ids": [i["id"] for i in items]}, **extra},
    )
    assert r.status_code == 201, r.text
    for limiter in (orders_router._per_ip, orders_router._per_phone):
        limiter.reset()
    return r.json()  # type: ignore[no-any-return]


def _pieces(token: str, client: TestClient, lines: list[tuple[str, int, str]], **extra: Any) -> Any:
    body: dict[str, Any] = {
        "name": "Ramesh Gupta",
        "phone": "98765 43210",
        "lines": [{"item_id": i, "qty": q, "unit": u} for i, q, u in lines],
        **extra,
    }
    return client.post(f"/api/public/catalog/{token}/orders", json=body)


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], list[dict]]:
    client, h, _ = catalog_client
    items = [
        _item(client, h, "Beer Mug 480 ML", "1256", pack_qty=6, sku="100500"),
        _item(client, h, "Single Bowl", "99", sku="100501"),  # sold singly: no pack size
    ]
    return client, h, items


def test_one_rule_for_the_piece_price() -> None:
    assert piece_rate(Decimal("1256"), 6) == Decimal("209.33")  # divided, to the paisa
    assert piece_rate(Decimal("1256"), 6, 10) == Decimal("230.27")  # +10% loose-piece premium
    assert piece_rate(Decimal("100"), 1) == Decimal("100.00")


def test_the_page_shows_a_piece_price_only_for_items_with_a_pack(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True)
    v = client.get(f"/api/public/catalog/{link['token']}").json()
    assert (v["allow_pieces"], v["price_basis"]) == (True, "pack")
    by = {i["name"]: i for i in v["items"]}
    assert by["Beer Mug 480 ML"]["price"] == "1256.00"
    assert by["Beer Mug 480 ML"]["piece_price"] == "209.33"
    assert by["Single Bowl"]["piece_price"] is None


def test_the_premium_only_applies_where_pieces_can_be_bought(shop) -> None:
    client, h, items = shop
    open_ = _link(client, h, items, allow_pieces=True, piece_premium_pct=10)
    closed = _link(client, h, items, allow_pieces=False, piece_premium_pct=10, price_basis="piece")
    on = client.get(f"/api/public/catalog/{open_['token']}").json()
    off = client.get(f"/api/public/catalog/{closed['token']}").json()
    assert next(i for i in on["items"] if i["code"] == "100500")["piece_price"] == "230.27"
    assert next(i for i in off["items"] if i["code"] == "100500")["piece_price"] == "209.33"
    assert off["price_basis"] == "piece" and off["allow_pieces"] is False


def test_a_piece_order_is_priced_per_piece_on_the_server(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True, piece_premium_pct=10)
    r = _pieces(link["token"], client, [(items[0]["id"], 4, "piece")])
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "921.08"  # 4 x 230.27
    with SessionLocal() as s:
        ln = s.scalar(select(CustomerOrderLine))
        assert (ln.unit, ln.qty, float(ln.rate), float(ln.amount)) == ("piece", 4, 230.27, 921.08)
    detail = client.get(
        f"/api/orders/{client.get('/api/orders', headers=h).json()[0]['id']}", headers=h
    ).json()
    assert detail["lines"][0]["unit"] == "piece"
    # compared piece to piece, so a piece line does not look as if its price had moved
    assert detail["lines"][0]["current_rate"] == "230.27"


def test_packs_still_work_and_a_mix_of_items_is_fine(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True)
    r = _pieces(link["token"], client, [(items[0]["id"], 2, "pack"), (items[1]["id"], 1, "pack")])
    assert r.status_code == 201 and r.json()["total"] == "2611.00"
    r2 = _pieces(
        link["token"],
        client,
        [(items[0]["id"], 1, "piece"), (items[1]["id"], 2, "pack")],
        phone="9111111111",
    )
    assert r2.status_code == 201 and r2.json()["total"] == "407.33"  # 209.33 + 2 x 99


def test_pieces_are_refused_when_the_link_does_not_allow_them(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items)  # allow_pieces defaults to off
    r = _pieces(link["token"], client, [(items[0]["id"], 1, "piece")])
    assert r.status_code == 422 and "whole packs" in r.json()["detail"]


def test_an_item_sold_singly_cannot_be_ordered_by_the_piece(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True)
    assert _pieces(link["token"], client, [(items[1]["id"], 1, "piece")]).status_code == 422


def test_one_item_cannot_be_ordered_in_both_units(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True)
    r = _pieces(link["token"], client, [(items[0]["id"], 1, "pack"), (items[0]["id"], 2, "piece")])
    assert r.status_code == 422 and "not both" in r.json()["detail"]


def test_a_pack_and_a_piece_order_for_the_same_things_are_not_one_double_tap(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True)
    a = _pieces(link["token"], client, [(items[0]["id"], 1, "pack")])
    b = _pieces(link["token"], client, [(items[0]["id"], 1, "piece")])
    assert a.json()["number"] != b.json()["number"] and not b.json().get("already_placed")


def test_the_minimum_order_counts_piece_amounts(shop) -> None:
    client, h, items = shop
    client.patch("/api/tenant", headers=h, json={"order_min_value": "500"})
    link = _link(client, h, items, allow_pieces=True)
    low = _pieces(link["token"], client, [(items[0]["id"], 2, "piece")])  # 418.66
    assert low.status_code == 422 and "minimum order" in low.json()["detail"]
    ok = _pieces(link["token"], client, [(items[0]["id"], 3, "piece")])  # 627.99
    assert ok.status_code == 201


def test_the_invoice_counts_loose_pieces_in_pieces(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True)
    _pieces(link["token"], client, [(items[0]["id"], 4, "piece"), (items[1]["id"], 1, "pack")])
    oid = client.get("/api/orders", headers=h).json()[0]["id"]
    client.post(f"/api/orders/{oid}/accept", headers=h)
    client.post(f"/api/orders/{oid}/customer", headers=h, json={"create_new": True})
    assert client.post(f"/api/orders/{oid}/invoice", headers=h).status_code == 200
    inv_id = client.get(f"/api/orders/{oid}", headers=h).json()["invoice_id"]
    lines = {
        ln["description"]: ln
        for ln in client.get(f"/api/invoices/{inv_id}", headers=h).json()["lines"]
    }
    loose = lines["Beer Mug 480 ML (loose pieces)"]
    assert (loose["quantity"], loose["uom"], loose["unit_rate"]) == ("4.000", "nos", "209.33")
    assert lines["Single Bowl"]["unit_rate"] == "99.00"


def test_the_link_remembers_its_piece_settings_and_they_can_be_changed(shop) -> None:
    client, h, items = shop
    link = _link(client, h, items, allow_pieces=True, piece_premium_pct=5, price_basis="piece")
    assert (link["allow_pieces"], link["piece_premium_pct"], link["price_basis"]) == (
        True,
        5,
        "piece",
    )
    patched = client.patch(
        f"{LINKS}/{link['id']}", headers=h, json={"allow_pieces": False, "piece_premium_pct": 12.5}
    ).json()
    assert (patched["allow_pieces"], patched["piece_premium_pct"], patched["price_basis"]) == (
        False,
        12.5,
        "piece",
    )
    assert (
        client.post(
            LINKS,
            headers=h,
            json={"title": "x", "selection": {"ids": [items[0]["id"]]}, "piece_premium_pct": 150},
        ).status_code
        == 422
    )


def test_the_catalog_pdf_can_carry_the_premium_too() -> None:
    from app.schemas_customer_catalog import CatalogLayout
    from app.services.catalog.catalog_pdf import piece_price
    from app.services.catalog.item_catalogs import layout_options

    opts = layout_options(CatalogLayout(price_basis="piece", piece_premium_pct=10))
    assert opts.piece_premium_pct == 10
    assert piece_price(Decimal("1256"), 6, opts.piece_premium_pct) == Decimal("230.27")
    assert CatalogLayout().piece_premium_pct == 0


def test_a_link_sets_how_the_page_is_laid_out(shop) -> None:
    client, h, items = shop
    plain = _link(client, h, items)
    assert (plain["columns"], plain["show_code"], plain["show_pack_info"]) == (2, True, True)
    link = _link(client, h, items, columns=1, show_code=False, show_pack_info=False)
    v = client.get(f"/api/public/catalog/{link['token']}").json()
    assert (v["columns"], v["show_code"], v["show_pack_info"]) == (1, False, False)
    patched = client.patch(f"{LINKS}/{link['id']}", headers=h, json={"columns": 3}).json()
    assert patched["columns"] == 3 and patched["show_code"] is False
    for bad in (0, 4):
        r = client.post(
            LINKS,
            headers=h,
            json={"title": "x", "selection": {"ids": [items[0]["id"]]}, "columns": bad},
        )
        assert r.status_code == 422

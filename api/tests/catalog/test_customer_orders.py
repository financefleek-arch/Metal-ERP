"""Customer ordering O2: placing an order from a share link."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CustomerOrder, CustomerOrderLine, Item, Party
from app.routers import orders as orders_router
from tests.catalog.conftest import auth, register
from tests.catalog.test_api_import import CatalogEnv

LINKS = "/api/share-links"
PHONE = "98765 43210"


def _item(client: TestClient, h: dict[str, str], name: str, rate: str | None, **extra: Any) -> dict:
    body: dict[str, Any] = {"name": name, "uom": "nos", **extra}
    if rate is not None:
        body["default_rate"] = rate
    r = client.post("/api/items", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], list[dict], str]:
    client, h, _ = catalog_client
    items = [
        _item(client, h, "Beer Mug 480 ML", "1256", pack_qty=6, sku="100500"),
        _item(client, h, "Juice Glass 190 ML", "190", pack_qty=6, sku="100501"),
        _item(client, h, "Sold Out Bowl", "99", sku="100503", availability="out_of_stock"),
    ]
    link = client.post(
        LINKS,
        headers=h,
        json={"title": "Range", "selection": {"ids": [i["id"] for i in items]}},
    ).json()
    for limiter in (orders_router._per_ip, orders_router._per_phone):
        limiter.reset()
    return client, h, items, link["token"]


def _order(token: str, client: TestClient, lines: list[tuple[str, int]], **extra: Any) -> Any:
    body: dict[str, Any] = {
        "name": "Ramesh Gupta",
        "phone": PHONE,
        "wa_opt_in": True,
        "lines": [{"item_id": i, "qty": q} for i, q in lines],
        **extra,
    }
    return client.post(f"/api/public/catalog/{token}/orders", json=body)  # no login


def test_a_customer_places_an_order_and_the_server_prices_it(shop) -> None:
    client, h, items, token = shop
    r = _order(token, client, [(items[0]["id"], 2), (items[1]["id"], 3)], firm="Gupta Traders")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["number"] == "ORD-0001" and out["total"] == "3082.00" and out["item_count"] == 2
    with SessionLocal() as s:
        o = s.scalar(select(CustomerOrder))
        assert (o.status, o.customer_phone, o.wa_opt_in, o.customer_firm) == (
            "new",
            "+919876543210",
            True,
            "Gupta Traders",
        )
        lines = {ln.name: ln for ln in s.scalars(select(CustomerOrderLine))}
        assert lines["Beer Mug 480 ML"].qty == 2 and float(lines["Beer Mug 480 ML"].rate) == 1256
        assert float(lines["Juice Glass 190 ML"].amount) == 570
    # the shop sees it
    listed = client.get("/api/orders", headers=h).json()
    assert [o["number"] for o in listed] == ["ORD-0001"] and listed[0]["status"] == "new"
    detail = client.get(f"/api/orders/{listed[0]['id']}", headers=h).json()
    assert len(detail["lines"]) == 2 and detail["link_title"] == "Range"
    assert client.get("/api/orders/counts", headers=h).json() == {"new": 1}


def test_order_numbers_count_up_per_shop_and_are_not_invoice_numbers(shop) -> None:
    client, h, items, token = shop
    a = _order(token, client, [(items[0]["id"], 1)], phone="9111111111").json()
    b = _order(token, client, [(items[0]["id"], 1)], phone="9222222222").json()
    assert (a["number"], b["number"]) == ("ORD-0001", "ORD-0002")


def test_a_double_tap_makes_one_order(shop) -> None:
    client, h, items, token = shop
    first = _order(token, client, [(items[0]["id"], 2)]).json()
    again = _order(token, client, [(items[0]["id"], 2)])
    assert again.status_code == 201 and again.json()["already_placed"] is True
    assert again.json()["number"] == first["number"]
    # a different basket is a different order
    other = _order(token, client, [(items[0]["id"], 3)]).json()
    assert other["number"] == "ORD-0002"


def test_only_what_the_link_offers_can_be_ordered(shop) -> None:
    client, h, items, token = shop
    r = _order(token, client, [(items[0]["id"], 1), (items[2]["id"], 1)])  # out of stock
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["unavailable"][0]["name"] == "Sold Out Bowl"
    with SessionLocal() as s:
        assert s.scalar(select(CustomerOrder.id)) is None  # nothing was saved
    # an item that was never in the link
    outsider = _item(client, h, "Not In The Link", "50")
    assert _order(token, client, [(outsider["id"], 1)]).status_code == 409
    # another firm's item id is just "unavailable"
    assert _order(token, client, [("00000000-0000-0000-0000-000000000000", 1)]).status_code == 409


def test_prices_come_from_the_item_not_the_browser(shop) -> None:
    client, h, items, token = shop
    client.patch(f"/api/items/{items[0]['id']}", headers=h, json={"default_rate": "1500"})
    body = {
        "name": "Ramesh",
        "phone": PHONE,
        "lines": [{"item_id": items[0]["id"], "qty": 1, "rate": "1"}],  # a forged rate is ignored
    }
    out = client.post(f"/api/public/catalog/{token}/orders", json=body).json()
    assert out["total"] == "1500.00"


def test_the_minimum_order_is_enforced(shop) -> None:
    client, h, items, token = shop
    client.patch("/api/tenant", headers=h, json={"order_min_value": "2000"})
    low = _order(token, client, [(items[1]["id"], 1)])
    assert low.status_code == 422 and "minimum order is" in low.json()["detail"].lower()
    ok = _order(token, client, [(items[0]["id"], 2)])
    assert ok.status_code == 201


def test_bad_input_is_refused(shop) -> None:
    client, h, items, token = shop
    line = [(items[0]["id"], 1)]
    assert _order(token, client, line, phone="12").status_code == 422
    assert _order(token, client, line, name=" ").status_code == 422
    assert _order(token, client, [(items[0]["id"], 0)]).status_code == 422
    assert _order(token, client, [(items[0]["id"], 1000)]).status_code == 422
    assert _order(token, client, []).status_code == 422
    assert _order(token, client, line, website="http://spam.example").status_code == 400
    assert client.post("/api/public/catalog/nope/orders", json={}).status_code in (404, 422)
    with SessionLocal() as s:
        assert s.scalar(select(CustomerOrder.id)) is None


def test_a_stopped_link_takes_no_orders(shop) -> None:
    client, h, items, token = shop
    link_id = client.get(LINKS, headers=h).json()[0]["id"]
    client.patch(f"{LINKS}/{link_id}", headers=h, json={"revoked": True})
    assert _order(token, client, [(items[0]["id"], 1)]).status_code == 404


def test_a_known_phone_is_suggested_as_the_customer(shop) -> None:
    client, h, items, token = shop
    party = client.post(
        "/api/parties",
        headers=h,
        json={"legal_name": "Gupta Traders", "role": "customer", "phone": "+919876543210"},
    ).json()
    _order(token, client, [(items[0]["id"], 1)])
    o = client.get("/api/orders", headers=h).json()[0]
    assert o["matched_party_id"] == party["id"] and o["matched_party_name"] == "Gupta Traders"
    # an unknown number is just a new contact
    _order(token, client, [(items[0]["id"], 1)], phone="9000000001")
    first = client.get("/api/orders", headers=h).json()[0]
    assert first["matched_party_id"] is None


def test_a_customer_can_follow_their_order_by_its_token(shop) -> None:
    client, h, items, token = shop
    placed = _order(token, client, [(items[0]["id"], 1)]).json()
    r = client.get(f"/api/public/orders/{placed['status_token']}")  # no login
    assert r.status_code == 200, r.text
    v = r.json()
    assert (
        v["number"] == "ORD-0001"
        and v["status"] == "new"
        and "shop will confirm" in v["status_text"]
    )
    assert v["lines"][0]["name"] == "Beer Mug 480 ML" and v["shop_name"]
    text = str(v).lower()
    for secret in ("tenant", "matched", "dedupe", "party", "wa_opt", "customer_phone", "cost"):
        assert secret not in text, secret
    assert client.get("/api/public/orders/nonsense").status_code == 404


def test_orders_are_rate_limited_per_phone_and_address(
    shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, items, token = shop
    monkeypatch.setattr(orders_router._per_phone, "limit", 2)
    codes = [_order(token, client, [(items[0]["id"], n)]).status_code for n in (1, 2, 3)]
    assert codes == [201, 201, 429]
    # a different phone is still fine
    assert _order(token, client, [(items[0]["id"], 4)], phone="9333333333").status_code == 201
    monkeypatch.setattr(orders_router._per_ip, "limit", 1)
    orders_router._per_ip.reset()
    assert _order(token, client, [(items[0]["id"], 5)], phone="9444444444").status_code == 201
    assert _order(token, client, [(items[0]["id"], 6)], phone="9555555555").status_code == 429


def test_only_your_own_firm_sees_orders(shop) -> None:
    client, h, items, token = shop
    _order(token, client, [(items[0]["id"], 1)])
    order_id = client.get("/api/orders", headers=h).json()[0]["id"]
    other = auth(register(client, "other@example.com"))
    assert client.get("/api/orders", headers=other).status_code == 404  # module off for that firm
    assert client.get(f"/api/orders/{order_id}", headers=other).status_code == 404


def test_deleting_an_item_or_a_customer_keeps_the_order(shop) -> None:
    """Orders mention items and customers by foreign key: deleting either must not break them."""
    client, h, items, token = shop
    client.post(
        "/api/parties",
        headers=h,
        json={"legal_name": "Gupta Traders", "role": "customer", "phone": "+919876543210"},
    )
    _order(token, client, [(items[1]["id"], 3)])
    party_id = client.get("/api/orders", headers=h).json()[0]["matched_party_id"]
    assert client.delete(f"/api/parties/{party_id}", headers=h).status_code == 204
    assert client.get("/api/orders", headers=h).json()[0]["matched_party_id"] is None
    assert client.delete(f"/api/items/{items[1]['id']}", headers=h).status_code == 204
    detail = client.get(
        f"/api/orders/{client.get('/api/orders', headers=h).json()[0]['id']}", headers=h
    )
    line = detail.json()["lines"][0]
    assert line["name"] == "Juice Glass 190 ML" and line["item_id"] is None
    with SessionLocal() as s:
        assert s.get(Item, items[1]["id"]) is None
        assert s.scalar(select(Party.id).where(Party.id == party_id)) is None


# --- O3: the shop reviews an order and makes a draft invoice ---------------------------------


def _placed(shop, **extra: Any) -> tuple[TestClient, dict[str, str], list[dict], str]:
    client, h, items, token = shop
    _order(token, client, [(items[0]["id"], 2), (items[1]["id"], 3)], **extra)
    oid = client.get("/api/orders", headers=h).json()[0]["id"]
    return client, h, items, oid


def _lines(*pairs: tuple[str, int]) -> dict:
    return {"lines": [{"item_id": i, "qty": q} for i, q in pairs]}


def test_the_shop_can_edit_quantities_remove_and_add_items(shop) -> None:
    client, h, items, oid = _placed(shop)
    url = f"/api/orders/{oid}"
    new = _item(client, h, "Late Addition", "100", sku="100600")
    # the new item is not in the link, but the shop may add any priced item
    body = _lines((items[0]["id"], 4), (items[1]["id"], 0), (new["id"], 5))
    r = client.put(f"{url}/lines", headers=h, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    by = {ln["name"]: ln for ln in d["lines"]}
    assert set(by) == {"Beer Mug 480 ML", "Late Addition"}
    assert by["Beer Mug 480 ML"]["amount"] == "5024.00"
    assert by["Late Addition"]["amount"] == "500.00"
    assert d["total"] == "5524.00" and d["item_count"] == 2
    # a line that stays keeps the rate the customer agreed to, even if the price moved
    client.patch(f"/api/items/{items[0]['id']}", headers=h, json={"default_rate": "2000"})
    d2 = client.put(f"{url}/lines", headers=h, json=_lines((items[0]["id"], 1))).json()
    assert d2["lines"][0]["rate"] == "1256.00" and d2["lines"][0]["current_rate"] == "2000.00"
    # an order cannot be emptied, and a refused change leaves it as it was
    both = _lines((items[0]["id"], 0), (new["id"], 0))
    assert client.put(f"{url}/lines", headers=h, json=both).status_code == 422
    assert len(client.get(url, headers=h).json()["lines"]) == 2


def test_accept_and_reject_with_a_reason(shop) -> None:
    client, h, items, oid = _placed(shop)
    url = f"/api/orders/{oid}"
    assert client.post(f"{url}/accept", headers=h).json()["status"] == "accepted"
    assert client.post(f"{url}/accept", headers=h).status_code == 409  # only a new order
    assert client.post(f"{url}/reject", headers=h, json={"reason": " "}).status_code == 422
    r = client.post(f"{url}/reject", headers=h, json={"reason": "Out of stock this month"})
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "Out of stock this month"
    # nothing more can be done with it
    assert (
        client.put(f"{url}/lines", headers=h, json=_lines((items[0]["id"], 1))).status_code == 409
    )
    assert client.post(f"{url}/invoice", headers=h).status_code == 409


def test_a_rejection_reason_reaches_the_customers_status_page(shop) -> None:
    client, h, items, token = shop
    placed = _order(token, client, [(items[0]["id"], 1)]).json()
    oid = client.get("/api/orders", headers=h).json()[0]["id"]
    client.post(f"/api/orders/{oid}/reject", headers=h, json={"reason": "We no longer stock this"})
    v = client.get(f"/api/public/orders/{placed['status_token']}").json()
    assert v["status"] == "rejected" and v["reject_reason"] == "We no longer stock this"


def test_the_customer_must_be_confirmed_before_an_invoice(shop) -> None:
    client, h, items, oid = _placed(shop)
    url = f"/api/orders/{oid}"
    r = client.post(f"{url}/invoice", headers=h)
    assert r.status_code == 422 and "who the customer is" in r.json()["detail"]
    assert client.post(f"{url}/customer", headers=h, json={}).status_code == 422
    made = client.post(f"{url}/customer", headers=h, json={"create_new": True})
    assert made.status_code == 200, made.text
    assert made.json()["matched_party_name"] == "Ramesh Gupta"
    # a second "new customer" with the same phone is refused: use the existing one
    again = client.post(f"{url}/customer", headers=h, json={"create_new": True})
    assert again.status_code == 409 and "Use that customer" in again.json()["detail"]
    other = client.post(
        "/api/parties", headers=h, json={"legal_name": "Other Co", "role": "customer"}
    ).json()
    pick = client.post(f"{url}/customer", headers=h, json={"party_id": other["id"]})
    assert pick.json()["matched_party_name"] == "Other Co"
    assert client.post(f"{url}/customer", headers=h, json={"party_id": "nope"}).status_code == 404


def test_a_draft_invoice_from_the_order_and_finalizing_marks_it_invoiced(shop) -> None:
    client, h, items, oid = _placed(shop, note="Deliver by Friday")
    url = f"/api/orders/{oid}"
    client.post(f"{url}/customer", headers=h, json={"create_new": True})
    r = client.post(f"{url}/invoice", headers=h)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["invoice_id"] and o["status"] == "accepted" and o["invoice_status"] == "draft"
    inv = client.get(f"/api/invoices/{o['invoice_id']}", headers=h).json()
    assert inv["status"] == "draft" and inv["party_id"] == o["matched_party_id"]
    assert "Deliver by Friday" in inv["notes"] and "ORD-0001" in inv["notes"]
    rows = {ln["description"]: ln for ln in inv["lines"]}
    mug = rows["Beer Mug 480 ML (pack of 6)"]
    assert float(mug["quantity"]) == 2 and float(mug["unit_rate"]) == 1256
    assert mug["item_id"] == items[0]["id"]
    assert float(inv["totals"]["subtotal"]) == 3082
    # one draft per order; the order can no longer be edited here
    assert client.post(f"{url}/invoice", headers=h).status_code == 409
    assert (
        client.put(f"{url}/lines", headers=h, json=_lines((items[0]["id"], 1))).status_code == 409
    )
    # finalizing the invoice marks the order invoiced
    fin = client.post(f"/api/invoices/{o['invoice_id']}/finalize", headers=h)
    assert fin.status_code == 200, fin.text
    assert client.get(url, headers=h).json()["status"] == "invoiced"


def test_deleting_the_draft_reopens_the_order(shop) -> None:
    client, h, items, oid = _placed(shop)
    url = f"/api/orders/{oid}"
    client.post(f"{url}/customer", headers=h, json={"create_new": True})
    inv_id = client.post(f"{url}/invoice", headers=h).json()["invoice_id"]
    assert client.delete(f"/api/invoices/{inv_id}", headers=h).status_code == 204
    o = client.get(url, headers=h).json()
    assert o["invoice_id"] is None and o["status"] == "accepted"
    assert client.post(f"{url}/invoice", headers=h).status_code == 200  # a new draft can be made


def test_review_hints_show_what_changed_since_the_order(shop) -> None:
    client, h, items, oid = _placed(shop)
    client.patch(
        f"/api/items/{items[1]['id']}",
        headers=h,
        json={"availability": "out_of_stock", "default_rate": "250"},
    )
    lines = {ln["name"]: ln for ln in client.get(f"/api/orders/{oid}", headers=h).json()["lines"]}
    mug = lines["Beer Mug 480 ML"]
    assert mug["available"] is True and mug["current_rate"] == "1256.00"
    juice = lines["Juice Glass 190 ML"]
    assert juice["available"] is False
    assert juice["current_rate"] == "250.00" and juice["rate"] == "190.00"


def test_the_order_actions_are_audited_and_need_a_writer(shop) -> None:
    from app.models import AuditLog, User

    client, h, items, oid = _placed(shop)
    url = f"/api/orders/{oid}"
    client.post(f"{url}/accept", headers=h)
    client.post(f"{url}/customer", headers=h, json={"create_new": True})
    client.post(f"{url}/invoice", headers=h)
    with SessionLocal() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.entity == "customer_order"))
        assert {"accept", "set_customer", "make_invoice"} <= {a.action for a in rows}
        u = s.scalar(select(User).where(User.email == "owner@catalog.example.com"))
        u.role = "viewer"
        s.commit()
    login = {"email": "owner@catalog.example.com", "password": "s3cret-pass"}
    vh = auth(client.post("/api/auth/login", json=login).json()["access_token"])
    assert client.get("/api/orders", headers=vh).status_code == 200
    assert client.post(f"{url}/accept", headers=vh).status_code == 403
    assert client.post(f"{url}/reject", headers=vh, json={"reason": "x"}).status_code == 403
    assert client.post(f"{url}/invoice", headers=vh).status_code == 403
    body = _lines((items[0]["id"], 1))
    assert client.put(f"{url}/lines", headers=vh, json=body).status_code == 403

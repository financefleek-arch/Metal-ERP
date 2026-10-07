"""Customer ordering O4: WhatsApp messages about orders (Meta is stubbed)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import WhatsappMessage
from app.routers import orders as orders_router
from app.services import whatsapp as wa
from tests.catalog.conftest import seed_hsn  # noqa: F401  (keeps FK-safe helpers importable)
from tests.catalog.test_api_import import CatalogEnv

LINKS = "/api/share-links"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for Meta: record each template call, send nothing."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(wa, "get_config", lambda *a, **k: wa.ResolvedConfig("t", "pn", "tok"))

    def fake(cfg: object, **kw: Any) -> str:
        calls.append(kw)
        return f"wamid.{len(calls)}"

    monkeypatch.setattr(wa, "_send_template_message", fake)
    return calls


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], dict, str]:
    client, h, _ = catalog_client
    item = client.post(
        "/api/items",
        headers=h,
        json={
            "name": "Beer Mug 480 ML",
            "uom": "nos",
            "default_rate": "1256",
            "pack_qty": 6,
            "sku": "100500",
        },
    ).json()
    link = client.post(
        LINKS, headers=h, json={"title": "Range", "selection": {"ids": [item["id"]]}}
    ).json()
    for limiter in (orders_router._per_ip, orders_router._per_phone):
        limiter.reset()
    return client, h, item, link["token"]


def _place(client: TestClient, token: str, item_id: str, **extra: Any) -> dict:
    body = {
        "name": "Ramesh Gupta",
        "phone": "98765 43210",
        "wa_opt_in": True,
        "lines": [{"item_id": item_id, "qty": 2}],
        **extra,
    }
    r = client.post(f"/api/public/catalog/{token}/orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _order_id(client: TestClient, h: dict[str, str]) -> str:
    return client.get("/api/orders", headers=h).json()[0]["id"]  # type: ignore[no-any-return]


def test_placing_an_order_tells_the_customer_and_the_shop(shop, sent) -> None:
    client, h, item, token = shop
    client.patch("/api/tenant", headers=h, json={"order_alert_phone": "+919000000000"})
    placed = _place(client, token, item["id"])
    by_tpl = {c["template_name"]: c for c in sent}
    assert set(by_tpl) == {"order_received", "new_order_alert"}
    cust = by_tpl["order_received"]
    assert cust["to_phone"] == "919876543210"
    assert cust["body_params"] == ["Ramesh", "Sample Traders", "ORD-0001", "1", "2512.00"]
    assert cust["button_url_param"] == placed["status_token"]  # the "View order" button
    alert = by_tpl["new_order_alert"]
    assert alert["to_phone"] == "919000000000" and alert["button_url_param"] is None
    assert alert["body_params"] == ["ORD-0001", "Ramesh Gupta", "1", "2512.00"]
    # the shop can see what was sent
    msgs = client.get(f"/api/orders/{_order_id(client, h)}", headers=h).json()["messages"]
    assert {(m["template"], m["status"], m["event"]) for m in msgs} == {
        ("order_received", "sent", "placed"),
        ("new_order_alert", "sent", "placed"),
    }


def test_no_whatsapp_box_ticked_means_no_message_to_the_customer(shop, sent) -> None:
    client, h, item, token = shop
    client.patch("/api/tenant", headers=h, json={"order_alert_phone": "+919000000000"})
    _place(client, token, item["id"], wa_opt_in=False)
    assert [c["template_name"] for c in sent] == ["new_order_alert"]  # the shop still hears


def test_no_alert_number_means_no_alert(shop, sent) -> None:
    client, h, item, token = shop
    _place(client, token, item["id"])
    assert [c["template_name"] for c in sent] == ["order_received"]


def test_a_double_tap_does_not_send_twice(shop, sent) -> None:
    client, h, item, token = shop
    _place(client, token, item["id"])
    again = _place(client, token, item["id"])
    assert again["already_placed"] is True
    assert [c["template_name"] for c in sent] == ["order_received"]


def test_accept_reject_and_invoice_each_send_one_update(shop, sent) -> None:
    client, h, item, token = shop
    _place(client, token, item["id"])
    sent.clear()
    oid = _order_id(client, h)
    url = f"/api/orders/{oid}"
    client.post(f"{url}/accept", headers=h)
    upd = sent[-1]
    assert upd["template_name"] == "order_update"
    assert upd["button_url_param"]  # the approved template has a "View Order" URL button
    assert upd["body_params"] == [
        "Ramesh",
        "ORD-0001",
        "confirmed",
        "We will send your invoice shortly.",
    ]
    client.post(f"{url}/customer", headers=h, json={"create_new": True})
    client.post(f"{url}/invoice", headers=h)
    inv_id = client.get(url, headers=h).json()["invoice_id"]
    sent.clear()
    assert client.post(f"/api/invoices/{inv_id}/finalize", headers=h).status_code == 200
    assert len(sent) == 1 and sent[0]["body_params"][2] == "invoiced"
    assert (
        sent[0]["body_params"][3].startswith("Invoice ") and "2512.00" in sent[0]["body_params"][3]
    )
    assert client.get(url, headers=h).json()["status"] == "invoiced"


def test_a_rejection_carries_the_reason(shop, sent) -> None:
    client, h, item, token = shop
    _place(client, token, item["id"])
    sent.clear()
    client.post(
        f"/api/orders/{_order_id(client, h)}/reject",
        headers=h,
        json={"reason": "Out of stock this month"},
    )
    assert sent[-1]["body_params"] == [
        "Ramesh",
        "ORD-0001",
        "cancelled",
        "Reason: Out of stock this month.",
    ]


def test_a_failed_send_never_breaks_the_order_and_is_recorded(
    shop, sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, item, token = shop

    def boom(cfg: object, **kw: Any) -> str:
        raise wa.WhatsappError("send failed: 400 template not approved")

    monkeypatch.setattr(wa, "_send_template_message", boom)
    placed = _place(client, token, item["id"])  # the customer's order still goes through
    assert placed["number"] == "ORD-0001"
    msgs = client.get(f"/api/orders/{_order_id(client, h)}", headers=h).json()["messages"]
    assert len(msgs) == 1 and msgs[0]["status"] == "failed" and "not approved" in msgs[0]["error"]


def test_a_firm_without_whatsapp_set_up_simply_skips(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, item, token = shop  # no stub: the firm has no WhatsApp configuration
    placed = _place(client, token, item["id"])
    assert placed["number"] == "ORD-0001"
    with SessionLocal() as s:
        assert s.scalar(select(WhatsappMessage.id)) is None


def test_a_retry_after_a_failure_can_send_but_a_success_is_not_repeated(
    shop, sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import order_notify

    client, h, item, token = shop
    _place(client, token, item["id"])
    oid = _order_id(client, h)
    assert len(sent) == 1
    order_notify.notify(oid, "placed")  # a second call for the same event
    assert len(sent) == 1


def test_a_mistyped_customer_phone_is_refused_not_sent_into_the_void() -> None:
    from fastapi import HTTPException

    from app.services.orders import clean_phone

    assert clean_phone("98509 96362") == "+919850996362"  # 10 digits
    assert clean_phone("+44 7700 900123") == "+447700900123"  # abroad, with its code
    for bad in ("98509996362", "1234567890", "98765"):  # 11 digits, not a mobile, too short
        with pytest.raises(HTTPException):
            clean_phone(bad)

"""Customer ordering O1: share links and the public catalog page's data."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, CatalogShareLink, User
from app.routers import share_links as share_router
from app.services import share_links as svc
from tests.catalog.conftest import auth, register
from tests.catalog.test_api_import import CatalogEnv

URL = "/api/share-links"


def _item(
    client: TestClient, h: dict[str, str], name: str, rate: str | None = "190", **extra: Any
) -> dict:
    body: dict[str, Any] = {"name": name, "uom": "nos", **extra}
    if rate is not None:
        body["default_rate"] = rate
    r = client.post("/api/items", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], list[dict]]:
    client, h, _ = catalog_client
    cat = client.post("/api/item-categories", headers=h, json={"name": "Drinkware"}).json()
    items = [
        _item(
            client,
            h,
            "Beer Mug 480 ML",
            "1256",
            pack_qty=6,
            sku="100500",
            category_id=cat["id"],
            last_purchase_rate=900,
        ),
        _item(
            client, h, "Juice Glass 190 ML", "190", pack_qty=6, sku="100501", category_id=cat["id"]
        ),
        _item(client, h, "Expected Jug", "300", sku="100502", availability="expected"),
        _item(client, h, "Sold Out Bowl", "99", sku="100503", availability="out_of_stock"),
        _item(client, h, "No Price Tray", None, sku="100504"),
    ]
    return client, h, items


def _make(client: TestClient, h: dict[str, str], items: list[dict], **extra: Any) -> dict:
    body = {"title": "Steel range", "selection": {"ids": [i["id"] for i in items]}, **extra}
    r = client.post(URL, headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _view(client: TestClient, token: str) -> Any:
    return client.get(f"/api/public/catalog/{token}")  # no login


def test_a_link_shows_only_what_a_customer_may_buy(shop) -> None:
    client, h, items = shop
    link = _make(client, h, items)
    assert link["path"] == f"/c/{link['token']}" and len(link["token"]) >= 16 and link["live"]
    r = _view(client, link["token"])  # note: no auth header
    assert r.status_code == 200, r.text
    v = r.json()
    names = [i["name"] for i in v["items"]]
    assert names == ["Beer Mug 480 ML", "Juice Glass 190 ML"]  # in stock and priced only
    assert v["firm_name"] and v["groups"] == ["Drinkware"] and v["title"] == "Steel range"
    mug = v["items"][0]
    assert mug["price"] == "1256.00" and mug["pack_qty"] == 6 and mug["code"] == "100500"
    assert mug["tag"] is None and mug["photo_url"] is None


def test_expected_items_only_when_the_link_allows_it(shop) -> None:
    client, h, items = shop
    plain = _view(client, _make(client, h, items)["token"]).json()
    assert "Expected Jug" not in [i["name"] for i in plain["items"]]
    open_ = _view(client, _make(client, h, items, include_expected=True)["token"]).json()
    jug = next(i for i in open_["items"] if i["name"] == "Expected Jug")
    assert jug["tag"] == "On order"


def test_the_public_page_never_leaks_internal_fields(shop) -> None:
    client, h, items = shop
    v = _view(client, _make(client, h, items, include_expected=True)["token"]).json()
    assert set(v) == {
        "title",
        "firm_name",
        "firm_phone",
        "terms_line",
        "min_order",
        "groups",
        "items",
    }
    for it in v["items"]:
        assert set(it) == set(svc.PUBLIC_ITEM_FIELDS)
    text = str(v).lower()
    # 900 is the purchase rate of the first item
    for secret in ("cost", "supplier", "margin", "last_purchase", "tally", "900", "tenant"):
        assert secret not in text, secret


def test_a_filter_link_follows_the_catalog_as_it_changes(shop) -> None:
    client, h, items = shop
    r = client.post(
        URL, headers=h, json={"title": "Everything", "selection": {"filter": {"q": "Glass"}}}
    )
    token = r.json()["token"]
    assert [i["name"] for i in _view(client, token).json()["items"]] == ["Juice Glass 190 ML"]
    client.patch(f"/api/items/{items[1]['id']}", headers=h, json={"default_rate": "250"})
    assert _view(client, token).json()["items"][0]["price"] == "250.00"
    client.patch(f"/api/items/{items[1]['id']}", headers=h, json={"availability": "out_of_stock"})
    assert _view(client, token).json()["items"] == []


def test_stopped_expired_and_unknown_links_all_answer_the_same(shop) -> None:
    client, h, items = shop
    link = _make(client, h, items)
    stopped = client.patch(f"{URL}/{link['id']}", headers=h, json={"revoked": True}).json()
    assert stopped["live"] is False and stopped["revoked_at"]
    gone = _view(client, link["token"])
    assert gone.status_code == 404
    resumed = client.patch(f"{URL}/{link['id']}", headers=h, json={"revoked": False}).json()
    assert resumed["live"] and _view(client, link["token"]).status_code == 200
    with SessionLocal() as s:  # expires in the past
        row = s.scalar(select(CatalogShareLink).where(CatalogShareLink.id == link["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    expired = _view(client, link["token"])
    assert expired.status_code == 404 and expired.json() == gone.json()
    assert _view(client, "nonsense").json() == gone.json()
    assert _view(client, "x" * 100).status_code == 404


def test_expiry_must_be_in_the_future_and_can_be_removed(shop) -> None:
    client, h, items = shop
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    body = {"title": "t", "selection": {"ids": [items[0]["id"]]}, "expires_at": past}
    assert client.post(URL, headers=h, json=body).status_code == 422
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    link = _make(client, h, items, expires_at=future)
    assert link["expires_at"]
    cleared = client.patch(f"{URL}/{link['id']}", headers=h, json={"expires_at": None}).json()
    assert cleared["expires_at"] is None


def test_views_are_counted_and_listed(shop) -> None:
    client, h, items = shop
    link = _make(client, h, items)
    for _ in range(3):
        _view(client, link["token"])
    listed = client.get(URL, headers=h).json()
    assert listed[0]["view_count"] == 3 and listed[0]["last_viewed_at"]
    assert _view(client, "nonsense").status_code == 404
    assert client.get(URL, headers=h).json()[0]["view_count"] == 3  # misses are not counted


def test_a_link_needs_items_and_firm_settings_show_on_the_page(shop) -> None:
    client, h, items = shop
    nothing = {"title": "t", "selection": {"filter": {"q": "zzzzzz"}}}
    assert client.post(URL, headers=h, json=nothing).status_code == 422
    r = client.patch(
        "/api/tenant",
        headers=h,
        json={"order_min_value": "2000", "order_terms_line": "Prices exclude GST"},
    )
    assert r.status_code == 200, r.text
    v = _view(client, _make(client, h, items)["token"]).json()
    assert v["min_order"] == "2000.00" and v["terms_line"] == "Prices exclude GST"
    assert client.patch("/api/tenant", headers=h, json={"order_min_value": "-5"}).status_code == 422


def test_photos_are_served_without_a_login(shop) -> None:
    client, h, items = shop
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), (200, 30, 30)).save(buf, format="JPEG")
    up = client.post(
        f"/api/items/{items[0]['id']}/photo",
        headers=h,
        files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert up.status_code == 200, up.text
    v = _view(client, _make(client, h, items)["token"]).json()
    mug = v["items"][0]
    assert mug["photo_url"] and mug["thumb_url"]
    assert client.get(mug["thumb_url"]).status_code == 200  # signed URL, no bearer header


def test_only_your_own_firm_and_writers_manage_links(shop) -> None:
    client, h, items = shop
    link = _make(client, h, items)
    other = auth(register(client, "other@example.com"))
    assert client.get(URL, headers=other).status_code == 404  # module off for that firm
    revoke = {"revoked": True}
    assert client.patch(f"{URL}/{link['id']}", headers=other, json=revoke).status_code == 404
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == "owner@catalog.example.com"))
        u.role = "viewer"
        s.commit()
    login = {"email": "owner@catalog.example.com", "password": "s3cret-pass"}
    vh = auth(client.post("/api/auth/login", json=login).json()["access_token"])
    assert client.get(URL, headers=vh).status_code == 200
    body = {"title": "t", "selection": {"ids": [items[0]["id"]]}}
    assert client.post(URL, headers=vh, json=body).status_code == 403
    assert client.patch(f"{URL}/{link['id']}", headers=vh, json=revoke).status_code == 403


def test_the_public_endpoint_is_rate_limited(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, items = shop
    token = _make(client, h, items)["token"]
    limiter = share_router._public_limiter
    limiter.reset()
    monkeypatch.setattr(limiter, "limit", 3)
    codes = [_view(client, token).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    limiter.reset()


def test_creating_and_stopping_are_audited(shop) -> None:
    client, h, items = shop
    link = _make(client, h, items)
    client.patch(f"{URL}/{link['id']}", headers=h, json={"revoked": True})
    with SessionLocal() as s:
        entries = s.scalars(select(AuditLog).where(AuditLog.entity == "share_link"))
        actions = {a.action for a in entries}
    assert actions == {"create", "stop"}

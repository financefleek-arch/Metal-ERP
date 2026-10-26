"""Barcode labels printed from items: check, codes, sync PDF, background job."""

from __future__ import annotations

from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item
from app.services.catalog import item_labels as svc
from tests.catalog.conftest import auth, register
from tests.catalog.test_api_import import CatalogEnv

URL = "/api/item-labels"


def _item(client: TestClient, h: dict[str, str], name: str, rate: str | None = "190", **extra: Any) -> dict:
    body: dict[str, Any] = {"name": name, "uom": "nos", **extra}
    if rate is not None:
        body["default_rate"] = rate
    r = client.post("/api/items", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _body(items: list[dict], **extra: Any) -> dict:
    return {"selection": {"ids": [i["id"] for i in items]}, **extra}


def _sku(item_id: str) -> str | None:
    with SessionLocal() as s:
        return s.scalar(select(Item.sku).where(Item.id == item_id))


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], list[dict]]:
    client, h, _ = catalog_client
    items = [
        _item(client, h, "Beer Mug 480 ML", "1256", sku="100500"),
        _item(client, h, "Juice Glass 190 ML", "190"),
        _item(client, h, "Dessert Bowl Set", None),
    ]
    return client, h, items


def test_check_counts_codes_and_prices(shop) -> None:
    client, h, items = shop
    r = client.post(f"{URL}/check", headers=h, json=_body(items, show_price=True, copies=2))
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["selected"], c["printable"], c["labels"]) == (3, 3, 6)
    assert c["no_code"] == 2 and c["no_price"] == 1 and c["inactive"] == 0
    # no price shown -> not a problem
    c = client.post(f"{URL}/check", headers=h, json=_body(items)).json()
    assert c["no_price"] == 0
    # leaving uncoded items out
    c = client.post(f"{URL}/check", headers=h, json=_body(items, assign_codes=False)).json()
    assert c["printable"] == 1


def test_make_assigns_codes_and_returns_pdf(shop) -> None:
    client, h, items = shop
    r = client.post(URL, headers=h, json=_body(items, show_price=True))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["X-Label-Count"] == "3"
    text = " ".join(p.get_text() for p in pymupdf.open(stream=r.content, filetype="pdf"))
    assert "100500" in text
    codes = [_sku(i["id"]) for i in items]
    assert codes[0] == "100500" and all(codes)
    assert len(set(codes)) == 3
    # printing again reuses the codes
    r2 = client.post(URL, headers=h, json=_body(items))
    assert r2.status_code == 200
    assert [_sku(i["id"]) for i in items] == codes


def test_make_without_assigning_leaves_uncoded_out(shop) -> None:
    client, h, items = shop
    r = client.post(URL, headers=h, json=_body(items, assign_codes=False))
    assert r.status_code == 200 and r.headers["X-Label-Count"] == "1"
    assert _sku(items[1]["id"]) is None
    only_uncoded = client.post(URL, headers=h, json=_body(items[1:], assign_codes=False))
    assert only_uncoded.status_code == 422


def test_archived_items_are_left_out(shop) -> None:
    client, h, items = shop
    r = client.patch(f"/api/items/{items[0]['id']}", headers=h, json={"status": "archived"})
    assert r.status_code == 200, r.text
    c = client.post(f"{URL}/check", headers=h, json=_body(items)).json()
    assert c["inactive"] == 1 and c["printable"] == 2


def test_filter_selection_and_preview(shop) -> None:
    client, h, _ = shop
    body = {"selection": {"filter": {"q": "glass"}}}
    c = client.post(f"{URL}/check", headers=h, json=body).json()
    assert c["selected"] == 1
    p = client.post(f"{URL}/preview", headers=h, json=body)
    assert p.status_code == 200 and p.content[:4] == b"\x89PNG"


def test_big_print_runs_as_job(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, items = shop
    monkeypatch.setattr(svc, "SYNC_LABEL_LIMIT", 2)
    r = client.post(URL, headers=h, json=_body(items))
    assert r.status_code == 202, r.text
    j = client.get(f"{URL}/jobs/{r.json()['id']}", headers=h).json()
    assert j["status"] == "done" and j["total"] == 3, j
    f = client.get(f"{URL}/jobs/{r.json()['id']}/file", headers=h)
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf"


def test_other_firm_cannot_label_my_items(shop) -> None:
    client, h, items = shop
    other = auth(register(client, "other@example.com"))
    r = client.post(f"{URL}/check", headers=other, json=_body(items))
    assert r.status_code == 404  # the catalog module is off for that firm


def test_no_code_filter_lists_only_items_without_a_code(shop) -> None:
    client, h, items = shop
    r = client.get("/api/items?no_code=true", headers=h)
    assert r.status_code == 200, r.text
    assert {i["name"] for i in r.json()} == {"Juice Glass 190 ML", "Dessert Bowl Set"}
    n = client.post("/api/items/count", headers=h, json={"no_code": True}).json()["count"]
    assert n == 2


def test_assign_codes_numbers_the_uncoded_without_printing(shop) -> None:
    client, h, items = shop
    r = client.post(f"{URL}/assign-codes", headers=h, json=_body(items))
    assert r.status_code == 200, r.text
    assert r.json() == {"selected": 3, "assigned": 2, "already_coded": 1, "inactive": 0}
    codes = [_sku(i["id"]) for i in items]
    assert codes[0] == "100500"  # an existing code is never changed
    assert all(codes) and len(set(codes)) == 3
    # nothing left to do the second time
    again = client.post(f"{URL}/assign-codes", headers=h, json=_body(items)).json()
    assert again["assigned"] == 0 and again["already_coded"] == 3
    assert client.get("/api/items?no_code=true", headers=h).json() == []


def test_numbering_skips_a_number_someone_typed_in_by_hand(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    typed = _item(client, h, "Hand Coded Item", sku="100001")  # the first number the counter issues
    plain = _item(client, h, "Plain Item")
    r = client.post(f"{URL}/assign-codes", headers=h, json=_body([typed, plain]))
    assert r.json()["assigned"] == 1
    assert _sku(typed["id"]) == "100001"
    new = _sku(plain["id"])
    assert new and new != "100001"


def test_a_code_cannot_repeat_on_another_item(shop) -> None:
    client, h, items = shop
    dup = client.post(
        "/api/items", headers=h, json={"name": "Another Mug", "uom": "nos", "sku": "100500"}
    )
    assert dup.status_code == 409
    assert "Beer Mug 480 ML" in dup.json()["detail"]

    other = items[1]
    taken = client.patch(f"/api/items/{other['id']}", headers=h, json={"sku": " 100500 "})
    assert taken.status_code == 409
    free = client.patch(f"/api/items/{other['id']}", headers=h, json={"sku": "JG-190"})
    assert free.status_code == 200 and free.json()["sku"] == "JG-190"
    # keeping its own code is not a clash
    same = client.patch(f"/api/items/{other['id']}", headers=h, json={"sku": "JG-190"})
    assert same.status_code == 200
    # blank clears it
    blank = client.patch(f"/api/items/{other['id']}", headers=h, json={"sku": "  "})
    assert blank.status_code == 200 and blank.json()["sku"] is None

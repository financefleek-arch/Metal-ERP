"""Approving a bill means the goods arrived: its items become In stock."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Item
from tests.inward.test_api_flow import _upload_sugal


def test_approve_marks_matched_and_new_items_in_stock(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, h = inward_client
    bill = _upload_sugal(client, h, sugal_pdf_bytes)
    name = bill["lines"][0]["new_item_staged_json"]["name"]
    gone = bill["lines"][1]["new_item_staged_json"]["name"]

    # the first line's product is already in your items, off the shelf (came from a price list)
    mine = client.post("/api/items", headers=h, json={"name": name, "uom": "nos"}).json()
    client.patch(f"/api/items/{mine['id']}", headers=h, json={"availability": "out_of_stock"})
    # the second one was discontinued: arriving on a bill must not silently revive it
    dead = client.post("/api/items", headers=h, json={"name": gone, "uom": "nos"}).json()
    client.patch(f"/api/items/{dead['id']}", headers=h, json={"availability": "discontinued"})

    r = client.post(f"/api/inward-bills/{bill['id']}/re-extract", headers=h)
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert lines[0]["matched_item_id"] == mine["id"] and lines[1]["matched_item_id"] == dead["id"]

    ok = client.post(f"/api/inward-bills/{bill['id']}/approve", headers=h)
    assert ok.status_code == 200, ok.text
    with SessionLocal() as s:
        assert s.get(Item, mine["id"]).availability == "in_stock"
        assert s.get(Item, dead["id"]).availability == "discontinued"
        for item_id in ok.json()["created_item_ids"]:
            assert s.get(Item, item_id).availability == "in_stock"

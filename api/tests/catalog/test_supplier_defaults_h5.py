"""H5: supplier defaults, update item prices, undo add-to-items, not-added filter, replace photo."""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item
from tests.catalog.pdfs import TestCell, make_pdf
from tests.catalog.test_api_import import CatalogEnv, _items, _upload
from tests.catalog.test_products import _party

BASE = "/api/supplier-catalogs"


def _pdf() -> bytes:
    return make_pdf(
        [
            [
                TestCell(code="B1", name_lines=["BEER MUG 480 ML", "COL BOX 6 PC"]),
                TestCell(code="J1", name_lines=["DELI 190 ML JUICE GLASS", "COL BOX 6 PC"]),
                TestCell(code="X1", name_lines=["PLAIN THING", "COL BOX 2 PC"]),
            ]
        ]
    )


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], str]:
    client, h, _ = catalog_client
    return client, h, _party(client, h, "Sugal Glass House")


def _put(client: TestClient, h: dict[str, str], sup: str, **body: Any) -> Any:
    return client.put(f"{BASE}/suppliers/{sup}/defaults", headers=h, json=body)


def _items_of(ids: list[str]) -> list[Item]:
    with SessionLocal() as s:
        rows = list(s.scalars(select(Item).where(Item.id.in_(ids))))
        s.expunge_all()
        return rows


def test_defaults_start_empty_and_roundtrip(shop) -> None:
    client, h, sup = shop
    d = client.get(f"{BASE}/suppliers/{sup}/defaults", headers=h).json()
    assert d == {
        "bulk_margin_pct": None,
        "rounding_step": None,
        "group_map": {},
        "add_automatically": False,
        "mark_in_stock": False,
    }
    r = _put(client, h, sup, bulk_margin_pct="22.5", rounding_step=5, add_automatically=True)
    assert r.status_code == 200, r.text
    assert r.json()["bulk_margin_pct"] == "22.50" and r.json()["rounding_step"] == 5
    # only the fields sent change; the map merges and a blank name removes an entry
    _put(client, h, sup, group_map={"Beer Mugs": "Drinkware", "Trays": "Serving"})
    r = _put(client, h, sup, group_map={"trays": ""}, mark_in_stock=True)
    d = r.json()
    assert d["group_map"] == {"beer mugs": "Drinkware"}
    assert d["add_automatically"] is True and d["mark_in_stock"] is True
    assert d["bulk_margin_pct"] == "22.50"
    assert _put(client, h, sup, rounding_step=7).status_code == 422
    # not a supplier / another firm's party
    cust = _party(client, h, "A Customer", role="customer")
    assert client.get(f"{BASE}/suppliers/{cust}/defaults", headers=h).status_code == 422


def test_an_import_uses_the_suppliers_defaults(shop) -> None:
    client, h, sup = shop
    _put(client, h, sup, bulk_margin_pct="10", rounding_step=5)
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    assert cat["bulk_margin_pct"] == "10.00" and cat["rounding_step"] == 5
    for row in _items(client, h, cat["id"], limit=200):
        assert float(row["sell_price"]) % 5 == 0  # rounded up to 5
        assert float(row["sell_price"]) >= float(row["cost_price"]) * 1.10 - 1e-6
        assert row["item_id"] is None  # not added automatically


def test_group_map_renames_the_suggested_group(shop) -> None:
    client, h, sup = shop
    first = _upload(client, h, data=_pdf(), name="a.pdf").json()
    groups = {
        r["category_name"] for r in _items(client, h, first["id"], limit=200) if r["category_name"]
    }
    assert groups  # the rules grouped something
    name = sorted(groups)[0]
    _put(client, h, sup, group_map={name: "Renamed Group"})
    other = make_pdf(
        [
            [
                TestCell(code="B9", name_lines=["BEER MUG 480 ML", "COL BOX 6 PC"]),
                TestCell(code="J9", name_lines=["DELI 190 ML JUICE GLASS", "COL BOX 6 PC"]),
                TestCell(code="X9", name_lines=["PLAIN THING", "COL BOX 2 PC"]),
            ]
        ]
    )
    second = _upload(client, h, data=other, name="b.pdf", supplier_party_id=sup).json()
    names = {r["category_name"] for r in _items(client, h, second["id"], limit=200)}
    assert "Renamed Group" in names and name not in names


def test_add_automatically_creates_items_and_marks_them_in_stock(shop) -> None:
    client, h, sup = shop
    _put(client, h, sup, bulk_margin_pct="20", add_automatically=True, mark_in_stock=True)
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    rows = _items(client, h, cat["id"], limit=200)
    ids = [r["item_id"] for r in rows]
    assert all(ids)
    items = _items_of(ids)
    assert {i.availability for i in items} == {"in_stock"}
    # the item's selling rate is the catalog's selling price (margin applied first)
    by_id = {r["item_id"]: r for r in rows}
    for it in items:
        assert float(it.default_rate) == pytest.approx(float(by_id[it.id]["sell_price"]))


def test_add_automatically_without_mark_in_stock_starts_out_of_stock(shop) -> None:
    client, h, sup = shop
    _put(client, h, sup, add_automatically=True)
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    ids = [r["item_id"] for r in _items(client, h, cat["id"], limit=200)]
    assert {i.availability for i in _items_of(ids)} == {"out_of_stock"}


def test_update_item_prices_is_explicit_and_counts(shop) -> None:
    client, h, sup = shop
    _put(client, h, sup, add_automatically=True)
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    ids = [r["item_id"] for r in _items(client, h, cat["id"], limit=200)]
    before = {i.id: float(i.default_rate) for i in _items_of(ids)}
    # a margin change on the price list does not touch the items by itself
    r = client.patch(f"{BASE}/{cat['id']}", headers=h, json={"bulk_margin_pct": "50"})
    assert r.status_code == 200, r.text
    assert {i.id: float(i.default_rate) for i in _items_of(ids)} == before
    pre = client.post(f"{BASE}/{cat['id']}/prices/preview", headers=h, json={"all_included": True})
    assert pre.status_code == 200, pre.text
    assert pre.json()["total"] == 3 and pre.json()["changing"] == 3 and pre.json()["examples"]
    done = client.post(f"{BASE}/{cat['id']}/prices/apply", headers=h, json={"all_included": True})
    assert done.json() == {"updated": 3}
    rows = {r["item_id"]: r for r in _items(client, h, cat["id"], limit=200)}
    for it in _items_of(ids):
        assert float(it.default_rate) == pytest.approx(float(rows[it.id]["sell_price"]))
    url = f"{BASE}/{cat['id']}/prices/preview"
    again = client.post(url, headers=h, json={"all_included": True})
    assert again.json()["changing"] == 0


def test_undo_removes_only_untouched_new_items(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    r = client.post(f"{BASE}/{cat['id']}/promote", headers=h, json={"all_included": True})
    out = r.json()
    assert len(out["created_item_ids"]) == 3
    # one of them was already sent to Tally: it stays
    keep = out["created_item_ids"][0]
    with SessionLocal() as s:
        s.get(Item, keep).tally_status = "synced"
        s.commit()
    u = client.post(
        f"{BASE}/{cat['id']}/promote/undo", headers=h, json={"item_ids": out["created_item_ids"]}
    )
    assert u.json() == {"removed": 2, "kept": 1}
    rows = {r["id"]: r for r in _items(client, h, cat["id"], limit=200)}
    assert sum(1 for r in rows.values() if r["item_id"]) == 1


def test_mark_in_stock_flag_on_promote(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    r = client.post(
        f"{BASE}/{cat['id']}/promote", headers=h, json={"all_included": True, "mark_in_stock": True}
    )
    assert {i.availability for i in _items_of(r.json()["created_item_ids"])} == {"in_stock"}


def test_not_added_filter(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    rows = _items(client, h, cat["id"], limit=200)
    client.post(f"{BASE}/{cat['id']}/promote", headers=h, json={"ids": [rows[0]["id"]]})
    assert len(_items(client, h, cat["id"], not_added=True, limit=200)) == 2
    assert len(_items(client, h, cat["id"], not_added=False, limit=200)) == 1


def test_replace_photo_updates_row_and_its_item(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    rows = _items(client, h, cat["id"], limit=200)
    row = rows[0]
    client.post(f"{BASE}/{cat['id']}/promote", headers=h, json={"ids": [row["id"]]})
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 160, 40)).save(buf, format="JPEG")
    r = client.post(
        f"{BASE}/{cat['id']}/items/{row['id']}/photo",
        headers=h,
        files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    after = next(x for x in _items(client, h, cat["id"], limit=200) if x["id"] == row["id"])
    assert after["image_url"] and after["image_url"] != row["image_url"]
    item = _items_of([after["item_id"]])[0]
    assert item.primary_media_id  # the item's own photo changed too
    bad = client.post(
        f"{BASE}/{cat['id']}/items/{row['id']}/photo",
        headers=h,
        files={"file": ("p.jpg", b"not an image", "image/jpeg")},
    )
    assert bad.status_code == 422


# --- G23: HSN and GST default per group ---------------------------------------------------


def _cat(client: TestClient, h: dict[str, str], name: str, **extra: Any) -> dict[str, Any]:
    r = client.post("/api/item-categories", headers=h, json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_group_hsn_is_inherited_by_new_items(shop) -> None:
    client, h, _ = shop
    cat = _cat(client, h, "Drinkware", hsn_code="7013", gst_rate="12")
    assert cat["hsn_code"] == "7013" and float(cat["gst_rate"]) == 12.0
    r = client.post(
        "/api/items",
        headers=h,
        json={"name": "Tall Tumbler", "uom": "nos", "category_id": cat["id"]},
    )
    assert r.status_code == 201, r.text
    it = _items_of([r.json()["id"]])[0]
    assert it.hsn_code == "7013" and float(it.gst_rate) == 12.0
    # an explicit HSN wins
    r = client.post(
        "/api/items",
        headers=h,
        json={"name": "Odd Tumbler", "uom": "nos", "category_id": cat["id"], "hsn_code": "7010"},
    )
    assert _items_of([r.json()["id"]])[0].hsn_code == "7010"


def test_apply_group_hsn_fills_only_items_without_one(shop) -> None:
    client, h, _ = shop
    cat = _cat(client, h, "Kitchen")
    ids = []
    for name, hsn in (("Pan A", None), ("Pan B", None), ("Pan C", "7323")):
        body: dict[str, Any] = {"name": name, "uom": "nos", "category_id": cat["id"]}
        if hsn:
            body["hsn_code"] = hsn
        ids.append(client.post("/api/items", headers=h, json=body).json()["id"])
    assert client.post(f"/api/item-categories/{cat['id']}/apply-hsn", headers=h).status_code == 422
    r = client.patch(
        f"/api/item-categories/{cat['id']}", headers=h, json={"hsn_code": "7615", "gst_rate": "18"}
    )
    assert r.json()["items_without_hsn"] == 2
    assert client.post(f"/api/item-categories/{cat['id']}/apply-hsn", headers=h).json() == {
        "updated": 2
    }
    got = {i.name: (i.hsn_code, float(i.gst_rate)) for i in _items_of(ids)}
    assert got["Pan A"] == ("7615", 18.0) and got["Pan B"] == ("7615", 18.0)
    assert got["Pan C"][0] == "7323"
    # clearing, and a bad code
    assert (
        client.patch(
            f"/api/item-categories/{cat['id']}", headers=h, json={"hsn_code": "12"}
        ).status_code
        == 422
    )
    r = client.patch(f"/api/item-categories/{cat['id']}", headers=h, json={"hsn_code": None})
    assert r.json()["hsn_code"] is None


def test_promote_gives_new_items_the_groups_hsn(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    rows = _items(client, h, cat["id"], limit=200)
    gid = next(r["category_id"] for r in rows if r["category_id"])
    body = {"hsn_code": "7013", "gst_rate": "12"}
    client.patch(f"/api/item-categories/{gid}", headers=h, json=body)
    url = f"{BASE}/{cat['id']}/promote"
    out = client.post(url, headers=h, json={"all_included": True}).json()
    by_cat = {r["item_id"]: r["category_id"] for r in _items(client, h, cat["id"], limit=200)}
    for it in _items_of(out["created_item_ids"]):
        if by_cat[it.id] == gid:
            assert it.hsn_code == "7013" and float(it.gst_rate) == 12.0
        else:
            assert it.hsn_code is None


# --- G22: find and replace in item names -------------------------------------------------


def _mk(client: TestClient, h: dict[str, str], *names: str) -> list[str]:
    out = []
    for n in names:
        r = client.post("/api/items", headers=h, json={"name": n, "uom": "nos"})
        assert r.status_code == 201, r.text
        out.append(r.json()["id"])
    return out


def test_bulk_rename_previews_then_applies(shop) -> None:
    client, h, _ = shop
    ids = _mk(client, h, "SS Bowl 100234", "SS Plate 100235", "Glass Jug")
    body = {"ids": ids, "find": " 1002", "replace": " X1002"}
    pre = client.post("/api/items/bulk-rename?dry_run=true", headers=h, json=body).json()
    assert pre["dry_run"] and pre["changed"] == 2 and pre["unchanged"] == 1
    assert {i.name for i in _items_of(ids)} == {"SS Bowl 100234", "SS Plate 100235", "Glass Jug"}
    done = client.post("/api/items/bulk-rename", headers=h, json=body).json()
    assert done["changed"] == 2 and done["errors"] == 0
    assert {i.name for i in _items_of(ids)} == {"SS Bowl X100234", "SS Plate X100235", "Glass Jug"}


def test_bulk_rename_removes_text_and_tidies_spaces(shop) -> None:
    client, h, _ = shop
    ids = _mk(client, h, "Steel Tray (old) Large")
    r = client.post(
        "/api/items/bulk-rename", headers=h, json={"ids": ids, "find": "(old)", "replace": ""}
    ).json()
    assert r["changed"] == 1
    assert _items_of(ids)[0].name == "Steel Tray Large"


def test_bulk_rename_clash_empty_case_and_whole_word(shop) -> None:
    client, h, _ = shop
    ids = _mk(client, h, "Pot Big", "Pot Small", "Cap", "Tops")
    # two names would become the same name: the second one is reported, not saved
    r = client.post(
        "/api/items/bulk-rename",
        headers=h,
        json={"ids": ids[:2], "find": "Big", "replace": "Small"},
    ).json()
    assert r["changed"] == 0 and r["errors"] == 1
    assert "Pot Small" in r["rows"][0]["detail"]
    # a name that would be emptied
    r = client.post(
        "/api/items/bulk-rename", headers=h, json={"ids": [ids[2]], "find": "Cap", "replace": ""}
    ).json()
    assert r["errors"] == 1 and _items_of([ids[2]])[0].name == "Cap"
    # whole word only: "Tops" keeps its "ops"; case-insensitive by default
    r = client.post(
        "/api/items/bulk-rename",
        headers=h,
        json={"ids": [ids[3]], "find": "op", "replace": "OP", "whole_word": True},
    ).json()
    assert r["changed"] == 0
    body = {"ids": [ids[3]], "find": "tops", "replace": "Lids"}
    r = client.post("/api/items/bulk-rename", headers=h, json=body).json()
    assert r["changed"] == 1
    r = client.post(
        "/api/items/bulk-rename",
        headers=h,
        json={"ids": [ids[3]], "find": "lids", "replace": "X", "case_sensitive": True},
    ).json()
    assert r["changed"] == 0


def test_bulk_rename_by_filter(shop) -> None:
    client, h, _ = shop
    ids = _mk(client, h, "Zq Mug", "Zq Cup", "Other")
    r = client.post(
        "/api/items/bulk-rename",
        headers=h,
        json={"filter": {"q": "Zq"}, "find": "Zq", "replace": "Steel"},
    ).json()
    assert r["changed"] == 2
    assert {i.name for i in _items_of(ids)} == {"Steel Mug", "Steel Cup", "Other"}


# --- G31: two people editing the same price-list row -----------------------------------------


def test_a_stale_edit_is_refused_instead_of_overwriting(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    row = _items(client, h, cat["id"], limit=200)[0]
    assert row["updated_at"]
    url = f"{BASE}/{cat['id']}/items/{row['id']}"
    ok = client.patch(
        url, headers=h, json={"pack_qty": 4, "expected_updated_at": row["updated_at"]}
    )
    assert ok.status_code == 200, ok.text
    # the row has moved on: the editor that still holds the old version is told, not obeyed
    from datetime import datetime, timedelta

    old = (datetime.fromisoformat(ok.json()["updated_at"]) - timedelta(minutes=5)).isoformat()
    stale = client.patch(url, headers=h, json={"pack_qty": 9, "expected_updated_at": old})
    assert stale.status_code == 409 and "Someone else" in stale.json()["detail"]
    assert (
        next(r for r in _items(client, h, cat["id"], limit=200) if r["id"] == row["id"])["pack_qty"]
        == 4
    )
    # an edit made on the latest version goes through; one without a version still works
    fresh = client.patch(
        url, headers=h, json={"pack_qty": 5, "expected_updated_at": ok.json()["updated_at"]}
    )
    assert fresh.status_code == 200
    assert client.patch(url, headers=h, json={"pack_qty": 6}).status_code == 200


# --- G27: what changed since the last price list, and supplier price history --------------


def _priced(*cells: tuple[str, str, str]) -> bytes:
    """(supplier code, name, price line)"""
    return make_pdf(
        [[TestCell(code=c, name_lines=[n, "COL BOX 6 PC"], price_line=p) for c, n, p in cells]]
    )


def test_a_reimport_shows_which_prices_moved(shop) -> None:
    client, h, sup = shop
    first = _upload(
        client,
        h,
        data=_priced(
            ("AAA111", "BEER MUG 480 ML", "Rs 190 for 6 pcs IN STOCK"),
            ("BBB222", "JUICE GLASS 190 ML", "Rs 200 for 6 pcs IN STOCK"),
            ("CCC333", "PLAIN THING", "Rs 50 for 6 pcs IN STOCK"),
        ),
        name="a.pdf",
        supplier_party_id=sup,
    ).json()
    assert first["price_changes"] == 0
    assert {r["price_change"] for r in _items(client, h, first["id"], limit=200)} == {"new"}

    second = _upload(
        client,
        h,
        data=_priced(
            ("AAA111", "BEER MUG 480 ML", "Rs 210 for 6 pcs IN STOCK"),  # up
            ("BBB222", "JUICE GLASS 190 ML", "Rs 200 for 6 pcs IN STOCK"),  # same
            ("CCC333", "PLAIN THING", "Rs 45 for 6 pcs IN STOCK"),  # down
            ("DDD444", "A NEW THING", "Rs 60 for 6 pcs IN STOCK"),  # new
        ),
        name="b.pdf",
        supplier_party_id=sup,
    ).json()
    assert second["price_changes"] == 2
    rows = {r["supplier_code"]: r for r in _items(client, h, second["id"], limit=200)}
    assert {k: v["price_change"] for k, v in rows.items()} == {
        "AAA111": "up",
        "BBB222": "same",
        "CCC333": "down",
        "DDD444": "new",
    }
    assert float(rows["AAA111"]["previous_cost"]) == 190.0
    assert rows["BBB222"]["previous_cost"] is None
    changed = _items(client, h, second["id"], price_changed=True, limit=200)
    assert {r["supplier_code"] for r in changed} == {"AAA111", "CCC333"}
    detail = client.get(f"{BASE}/{second['id']}", headers=h).json()
    assert detail["price_change_count"] == 2


def test_last_paid_shows_on_a_row_once_a_bill_charged_it(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    row = _items(client, h, cat["id"], limit=200)[0]
    client.post(f"{BASE}/{cat['id']}/promote", headers=h, json={"ids": [row["id"]]})
    row = next(r for r in _items(client, h, cat["id"], limit=200) if r["id"] == row["id"])
    assert row["last_paid"] is None
    from app.models import SupplierPricePoint

    with SessionLocal() as s:
        s.add(
            SupplierPricePoint(
                tenant_id=s.get(Item, row["item_id"]).tenant_id,
                item_id=row["item_id"],
                source="bill",
                source_id="b1",
                price=123,
                on_date=date(2026, 1, 5),
            )
        )
        s.commit()
    row = next(r for r in _items(client, h, cat["id"], limit=200) if r["id"] == row["id"])
    assert float(row["last_paid"]) == 123.0 and row["last_paid_on"] == "2026-01-05"


# --- G18: photos that need a look ---------------------------------------------------------


def test_image_check_spots_bad_crops() -> None:
    import random

    from app.services.catalog import image_check

    def png(size: tuple[int, int], noise: bool = False) -> bytes:
        img = Image.new("RGB", size, (120, 30, 30))
        if noise:
            px = img.load()
            for x in range(size[0]):
                for y in range(size[1]):
                    px[x, y] = (
                        random.randint(0, 255),
                        random.randint(0, 255),
                        random.randint(0, 255),
                    )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    assert image_check.check(png((400, 300), noise=True)) is None
    assert image_check.check(png((60, 60), noise=True)) == "small"
    assert image_check.check(png((900, 150), noise=True)) == "odd_shape"
    assert image_check.check(png((400, 300))) == "blank"
    assert image_check.check(b"nope") == "unreadable"


def test_a_flagged_photo_can_be_cleared_or_replaced(shop) -> None:
    client, h, sup = shop
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    rows = _items(client, h, cat["id"], limit=200)
    from app.models import SupplierCatalogItem

    with SessionLocal() as s:
        # the tiny test pictures are flagged on their own: start clean, then flag two
        for r in s.scalars(select(SupplierCatalogItem)):
            r.image_flag = None
        for rid in (rows[0]["id"], rows[1]["id"]):
            s.get(SupplierCatalogItem, rid).image_flag = "small"
        s.commit()
    flagged = _items(client, h, cat["id"], photo_check=True, limit=200)
    assert {r["id"] for r in flagged} == {rows[0]["id"], rows[1]["id"]}
    assert flagged[0]["image_flag"] == "small"
    assert client.get(f"{BASE}/{cat['id']}", headers=h).json()["photo_check_count"] == 2
    url0 = f"{BASE}/{cat['id']}/items/{rows[0]['id']}"
    assert client.patch(url0, headers=h, json={"photo_ok": True}).json()["image_flag"] is None
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 160, 40)).save(buf, format="JPEG")
    r = client.post(
        f"{BASE}/{cat['id']}/items/{rows[1]['id']}/photo",
        headers=h,
        files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert r.status_code == 200
    assert _items(client, h, cat["id"], photo_check=True, limit=200) == []


def test_price_lines_with_no_picture_are_counted(shop) -> None:
    import pymupdf

    client, h, sup = shop
    doc = pymupdf.open(stream=_pdf(), filetype="pdf")
    doc[0].insert_text((60, 780), "PLAIN STEEL TRAY  Rs 55 for 6 pcs  (no photo)", fontsize=9)
    cat = _upload(client, h, data=doc.tobytes(), name="np.pdf", supplier_party_id=sup).json()
    assert cat["unread_price_lines"] == 1
    again = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    assert again["unread_price_lines"] == 0

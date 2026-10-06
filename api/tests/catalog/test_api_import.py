"""S1 API: upload + extract, review list, edits, bulk edits, groups, photos, delete."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.catalog.storage import LocalStorage, get_storage
from tests.catalog.conftest import SAMPLE_PDF, auth, enable_catalog_flag, register
from tests.catalog.pdfs import TestCell, make_pdf, text_only_pdf

CatalogEnv = tuple[TestClient, dict[str, str], str]
JPEG_MAGIC = bytes([0xFF, 0xD8])


def _upload(
    client: TestClient,
    h: dict[str, str],
    data: bytes | None = None,
    name: str = "glassware.pdf",
    **form: str,
) -> Any:
    return client.post(
        "/api/supplier-catalogs",
        headers=h,
        files={
            "file": (
                name,
                data if data is not None else SAMPLE_PDF.read_bytes(),
                "application/pdf",
            )
        },
        data=form,
    )


@pytest.fixture
def uploaded(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], dict[str, Any]]:
    client, h, _ = catalog_client
    r = _upload(client, h)
    assert r.status_code == 201, r.text
    return client, h, r.json()


def _items(client: TestClient, h: dict[str, str], cid: str, **params: Any) -> list[dict[str, Any]]:
    r = client.get(f"/api/supplier-catalogs/{cid}/items", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# --- upload -------------------------------------------------------------------


def test_upload_creates_catalog_with_items(uploaded) -> None:
    client, h, cat = uploaded
    assert cat["status"] == "ready"
    assert cat["page_count"] == 2
    assert cat["item_count"] == 24
    assert cat["title"] == "glassware"
    assert cat["supplier_party_id"] is None
    assert cat["bulk_margin_pct"] == "0.00"
    assert cat["already_exists"] is False
    assert len(_items(client, h, cat["id"], limit=200)) == 24


def test_codes_are_a_firm_wide_running_number_in_reading_order(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"], limit=200)
    codes = [int(i["code"]) for i in items]
    assert codes == list(range(100001, 100025))  # six digits, no group in the code
    assert all(i["product_id"] for i in items)
    assert items[0]["supplier_code"] == "AJ-1003"
    assert (items[0]["page_no"], items[0]["position"]) == (1, 1)


def test_items_carry_cleaned_fields_and_unmodified_price(uploaded) -> None:
    client, h, cat = uploaded
    first = _items(client, h, cat["id"])[0]
    assert first["display_name"] == "G K 7 PC Pudding SET"
    assert first["name_raw"].startswith("AJ-1003")
    assert first["carton_qty"] == 6
    assert first["pack_qty"] == 7
    assert first["cost_price"] == "299.00"
    assert first["sell_price"] == "299.00"  # no margin at import
    assert first["item_margin_pct"] is None
    assert first["included"] is True
    assert first["item_id"] is None


def test_same_file_twice_returns_the_existing_catalog(uploaded) -> None:
    client, h, cat = uploaded
    r = _upload(client, h)
    assert r.status_code == 200
    assert r.json()["id"] == cat["id"]
    assert r.json()["already_exists"] is True
    assert len(client.get("/api/supplier-catalogs", headers=h).json()) == 1
    # codes were not consumed again: the next new product carries on from 100025
    r2 = _upload(client, h, data=make_pdf([[TestCell(code="Q1")]]), name="q.pdf")
    assert _items(client, h, r2.json()["id"])[0]["code"] == "100025"


def test_title_can_be_given(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    r = _upload(client, h, title="Kitchen Glass Aug")
    assert r.status_code == 201 and r.json()["title"] == "Kitchen Glass Aug"


def test_the_firm_prefix_is_put_in_front_of_new_codes(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    client.patch("/api/tenant", headers=h, json={"catalog_code_prefix": "ks"})
    r = _upload(client, h, name="whatever.pdf")
    assert _items(client, h, r.json()["id"])[0]["code"] == "KS100001"


def test_second_catalog_never_reuses_a_code(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a = _upload(client, h).json()
    other = make_pdf([[TestCell(code="Z1"), TestCell(code="Z2")]])
    b = _upload(client, h, data=other, name="b.pdf").json()
    codes_a = {i["code"] for i in _items(client, h, a["id"], limit=200)}
    codes_b = {i["code"] for i in _items(client, h, b["id"])}
    assert len(codes_b) == 2 and not codes_a & codes_b
    assert a["id"] != b["id"]


def test_bad_uploads_are_rejected_with_clear_errors(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    r = _upload(client, h, data=b"hello, not a pdf", name="x.pdf")
    assert r.status_code == 415
    r = _upload(client, h, data=text_only_pdf())
    assert r.status_code == 422 and "No products found" in r.json()["detail"]
    assert client.get("/api/supplier-catalogs", headers=h).json() == []  # nothing half-created


def test_oversize_upload_is_413(
    catalog_client: CatalogEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, _ = catalog_client
    monkeypatch.setattr("app.routers.catalog.MAX_UPLOAD_BYTES", 1000)
    r = _upload(client, h, data=b"%PDF-" + b"0" * 2000)
    assert r.status_code == 413


def _stored_keys(catalog_id: str) -> tuple[set[str], str | None]:
    """(distinct photo keys, source pdf key) recorded for a catalog."""
    from app.db import SessionLocal
    from app.models import SupplierCatalog, SupplierCatalogItem

    with SessionLocal() as s:
        photos = {
            k
            for (k,) in s.query(SupplierCatalogItem.image_key).filter_by(catalog_id=catalog_id)
            if k
        }
        cat = s.get(SupplierCatalog, catalog_id)
        return photos, cat.source_key if cat else None


def test_photos_and_source_are_stored(uploaded, storage: LocalStorage) -> None:
    client, h, cat = uploaded
    photos, source = _stored_keys(cat["id"])
    assert len(photos) >= 20  # 24 cells, nearly all distinct photos
    assert all(storage.get(k)[:2] == JPEG_MAGIC for k in photos)
    assert source is not None
    assert storage.get(source) == SAMPLE_PDF.read_bytes()


# --- groups -------------------------------------------------------------------


def test_groups_are_created_automatically_and_listed(uploaded) -> None:
    client, h, cat = uploaded
    groups = client.get(f"/api/supplier-catalogs/{cat['id']}/groups", headers=h).json()
    names = [g["name"] for g in groups]
    assert "Beer Mugs" in names and "Bowls & Bowl Sets" in names
    assert sum(g["item_count"] for g in groups) == 24
    # the new groups exist as item categories too
    cats = {c["name"] for c in client.get("/api/item-categories", headers=h).json()}
    assert {"Beer Mugs", "Bowls & Bowl Sets"} <= cats
    # the unnamed bucket, if any, sorts last
    assert [g["name"] for g in groups if g["category_id"] is None] in ([], ["No group"])
    if any(g["category_id"] is None for g in groups):
        assert groups[-1]["category_id"] is None


def test_item_shows_its_group_name(uploaded) -> None:
    client, h, cat = uploaded
    mug = next(i for i in _items(client, h, cat["id"], limit=200) if i["supplier_code"] == "Y5813")
    assert mug["category_name"] == "Beer Mugs"


def test_suggest_only_policy_creates_no_categories(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    before = {c["name"] for c in client.get("/api/item-categories", headers=h).json()}
    client.patch("/api/tenant", headers=h, json={"catalog_group_create_policy": "suggest_only"})
    cat = _upload(client, h).json()
    after = {c["name"] for c in client.get("/api/item-categories", headers=h).json()}
    assert after == before
    items = _items(client, h, cat["id"], limit=200)
    assert all(i["category_id"] is None for i in items)
    mug = next(i for i in items if i["supplier_code"] == "Y5813")
    assert mug["suggested_group"] == "Beer Mugs"


def test_suggest_only_still_uses_a_group_that_already_exists(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    client.post("/api/item-categories", headers=h, json={"name": "beer mugs"})
    client.patch("/api/tenant", headers=h, json={"catalog_group_create_policy": "suggest_only"})
    cat = _upload(client, h).json()
    mug = next(i for i in _items(client, h, cat["id"], limit=200) if i["supplier_code"] == "Y5813")
    assert mug["category_name"] == "beer mugs"  # case-insensitive reuse
    assert mug["suggested_group"] is None


# --- list / filters / pagination ----------------------------------------------


def test_keyset_pagination_walks_every_item_once(uploaded) -> None:
    client, h, cat = uploaded
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 5}
        if cursor:
            params["cursor"] = cursor
        r = client.get(f"/api/supplier-catalogs/{cat['id']}/items", headers=h, params=params)
        assert r.headers["X-Total-Count"] == "24"
        seen += [i["id"] for i in r.json()]
        cursor = r.headers.get("X-Next-Cursor")
        if not cursor:
            break
    assert len(seen) == 24 == len(set(seen))


def test_search_filters_and_narrows_by_word(uploaded) -> None:
    client, h, cat = uploaded
    beer = _items(client, h, cat["id"], q="beer", limit=200)
    assert beer and all("beer" in (i["name_raw"] + i["display_name"]).lower() for i in beer)
    narrower = _items(client, h, cat["id"], q="beer yujing", limit=200)
    assert 0 < len(narrower) < len(beer)
    assert _items(client, h, cat["id"], q="y5813")[0]["supplier_code"] == "Y5813"
    some = _items(client, h, cat["id"])[0]["code"]
    assert some in [i["code"] for i in _items(client, h, cat["id"], q=some)]
    assert _items(client, h, cat["id"], q="zzzzzz") == []


def test_search_treats_percent_and_underscore_literally(uploaded) -> None:
    client, h, cat = uploaded
    assert _items(client, h, cat["id"], q="%") == []
    assert _items(client, h, cat["id"], q="_") == []


def test_group_and_included_filters(uploaded) -> None:
    client, h, cat = uploaded
    groups = client.get(f"/api/supplier-catalogs/{cat['id']}/groups", headers=h).json()
    g = next(x for x in groups if x["name"] == "Beer Mugs")
    got = _items(client, h, cat["id"], category_id=g["category_id"], limit=200)
    assert len(got) == g["item_count"]
    assert _items(client, h, cat["id"], included=False) == []
    assert len(_items(client, h, cat["id"], included=True, limit=200)) == 24


# --- single edit ---------------------------------------------------------------


def _patch(client: TestClient, h: dict[str, str], cid: str, iid: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/api/supplier-catalogs/{cid}/items/{iid}", headers=h, json=body)


def test_edit_name_pack_and_flags(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    r = _patch(
        client,
        h,
        cat["id"],
        it["id"],
        {
            "display_name": "  Pudding Set 7 Pc  ",
            "pack_qty": 6,
            "included": False,
            "brand": " Deli ",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["display_name"], out["pack_qty"], out["included"], out["brand"]) == (
        "Pudding Set 7 Pc",
        6,
        False,
        "Deli",
    )
    # persisted
    assert _items(client, h, cat["id"])[0]["display_name"] == "Pudding Set 7 Pc"


def test_edit_cost_recomputes_sell_price(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    out = _patch(client, h, cat["id"], it["id"], {"cost_price": "310.50"}).json()
    assert out["cost_price"] == "310.50"
    assert out["sell_price"] == "311.00"  # no margin, step 1, half up


def test_setting_a_new_group_creates_it_and_blank_clears_it(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    out = _patch(client, h, cat["id"], it["id"], {"group_name": "  Dessert  Sets "}).json()
    assert out["category_name"] == "Dessert Sets"
    cats = {c["name"] for c in client.get("/api/item-categories", headers=h).json()}
    assert "Dessert Sets" in cats
    cleared = _patch(client, h, cat["id"], it["id"], {"group_name": ""}).json()
    assert cleared["category_id"] is None


def test_user_named_group_is_created_even_under_suggest_only(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    client.patch("/api/tenant", headers=h, json={"catalog_group_create_policy": "suggest_only"})
    cat = _upload(client, h).json()
    it = _items(client, h, cat["id"])[0]
    out = _patch(client, h, cat["id"], it["id"], {"group_name": "My Group"}).json()
    assert out["category_name"] == "My Group"


@pytest.mark.parametrize("body", [{"pack_qty": 0}, {"cost_price": "-1"}, {"display_name": ""}])
def test_invalid_edits_are_422(uploaded, body: dict[str, Any]) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    assert _patch(client, h, cat["id"], it["id"], body).status_code == 422


# --- bulk edit -----------------------------------------------------------------


def _bulk(client: TestClient, h: dict[str, str], cid: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/api/supplier-catalogs/{cid}/items/bulk", headers=h, json=body)


def test_bulk_by_ids(uploaded) -> None:
    client, h, cat = uploaded
    ids = [i["id"] for i in _items(client, h, cat["id"])[:3]]
    r = _bulk(client, h, cat["id"], {"ids": ids, "changes": {"included": False}})
    assert r.json() == {"updated": 3}
    assert len(_items(client, h, cat["id"], included=False)) == 3


def test_bulk_by_filter_sets_a_group_on_everything_matching(uploaded) -> None:
    client, h, cat = uploaded
    n = len(_items(client, h, cat["id"], q="beer", limit=200))
    body = {"filter": {"q": "beer"}, "changes": {"group_name": "Tall Mugs"}}
    r = _bulk(client, h, cat["id"], body)
    assert r.json() == {"updated": n}
    groups = client.get(f"/api/supplier-catalogs/{cat['id']}/groups", headers=h).json()
    assert next(g for g in groups if g["name"] == "Tall Mugs")["item_count"] == n


def test_bulk_clear_group_for_a_selection(uploaded) -> None:
    client, h, cat = uploaded
    ids = [i["id"] for i in _items(client, h, cat["id"])[:2]]
    _bulk(client, h, cat["id"], {"ids": ids, "changes": {"group_name": None}})
    assert len(_items(client, h, cat["id"], no_group=True, limit=200)) >= 2


def test_bulk_needs_exactly_one_target_and_a_change(uploaded) -> None:
    client, h, cat = uploaded
    assert _bulk(client, h, cat["id"], {"changes": {"included": False}}).status_code == 422
    both = {"ids": ["x"], "filter": {}, "changes": {"included": False}}
    assert _bulk(client, h, cat["id"], both).status_code == 422
    assert _bulk(client, h, cat["id"], {"ids": ["x"], "changes": {}}).status_code == 422


def test_bulk_ids_from_another_catalog_are_ignored(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a = _upload(client, h).json()
    b = _upload(client, h, data=make_pdf([[TestCell(code="Q1")]]), name="b.pdf").json()
    a_ids = [i["id"] for i in _items(client, h, a["id"])[:2]]
    r = _bulk(client, h, b["id"], {"ids": a_ids, "changes": {"included": False}})
    assert r.json() == {"updated": 0}
    assert _items(client, h, a["id"], included=False) == []


# --- photos --------------------------------------------------------------------


def test_photo_url_serves_the_jpeg_without_a_bearer_header(uploaded) -> None:
    client, h, cat = uploaded
    url = _items(client, h, cat["id"])[0]["image_url"]
    assert url.startswith(f"/api/supplier-catalogs/{cat['id']}/items/")
    r = client.get(url)  # no Authorization header, as an <img> tag would
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == JPEG_MAGIC
    assert "max-age" in r.headers["cache-control"]


def test_photo_url_is_stable_within_the_hour(uploaded) -> None:
    client, h, cat = uploaded
    a = _items(client, h, cat["id"])[0]["image_url"]
    b = _items(client, h, cat["id"])[0]["image_url"]
    assert a == b


def test_tampered_or_expired_photo_links_are_refused(uploaded) -> None:
    client, h, cat = uploaded
    url = _items(client, h, cat["id"])[0]["image_url"]
    base, query = url.split("?")
    exp = query.split("&")[0].removeprefix("e=")
    assert client.get(f"{base}?e={exp}&s=deadbeef").status_code == 403
    assert client.get(f"{base}?e=1&s={query.split('s=')[1]}").status_code == 403
    assert client.get(base).status_code == 422  # missing signature
    other = url.replace(cat["id"], cat["id"])  # same catalog, different item id, same sig
    item2 = _items(client, h, cat["id"])[1]["id"]
    swapped = base.rsplit("/", 2)[0] + f"/{item2}/image?{query}"
    assert client.get(swapped).status_code == 403
    assert other  # keep linters quiet about the unused no-op


# --- patch / delete catalog ----------------------------------------------------


def test_rename_catalog(uploaded) -> None:
    client, h, cat = uploaded
    r = client.patch(f"/api/supplier-catalogs/{cat['id']}", headers=h, json={"title": "Glass Q3"})
    assert r.status_code == 200 and r.json()["title"] == "Glass Q3"
    got = client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h).json()
    assert got["title"] == "Glass Q3"


def test_delete_catalog_removes_items_and_stored_files(uploaded, storage: LocalStorage) -> None:
    client, h, cat = uploaded
    photos, source = _stored_keys(cat["id"])
    assert source is not None and storage.get(source)
    assert client.delete(f"/api/supplier-catalogs/{cat['id']}", headers=h).status_code == 204
    assert client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h).status_code == 404
    assert client.get("/api/supplier-catalogs", headers=h).json() == []
    for key in photos | {source}:
        with pytest.raises(FileNotFoundError):
            storage.get(key)
    # the file can be uploaded again afterwards
    assert _upload(client, h).status_code == 201


def test_delete_is_blocked_once_items_are_promoted(uploaded) -> None:
    from app.db import SessionLocal
    from app.models import Item, SupplierCatalogItem

    client, h, cat = uploaded
    with SessionLocal() as s:
        row = s.query(SupplierCatalogItem).filter_by(catalog_id=cat["id"]).first()
        assert row is not None
        item = Item(tenant_id=row.tenant_id, name="x", name_normalized="x")
        s.add(item)
        s.flush()
        row.item_id = item.id
        s.commit()
    r = client.delete(f"/api/supplier-catalogs/{cat['id']}", headers=h)
    assert r.status_code == 409
    assert "item list" in r.json()["detail"]


# --- isolation + permissions ---------------------------------------------------


def test_another_tenant_cannot_see_or_touch_a_catalog(uploaded) -> None:
    client, h, cat = uploaded
    other = auth(register(client, "other@catalog.example.com"))
    enable_catalog_flag(client, other["Authorization"].removeprefix("Bearer "))
    assert client.get("/api/supplier-catalogs", headers=other).json() == []
    cid = cat["id"]
    it = _items(client, h, cid)[0]["id"]
    for method, path, body in [
        ("GET", f"/api/supplier-catalogs/{cid}", None),
        ("GET", f"/api/supplier-catalogs/{cid}/items", None),
        ("GET", f"/api/supplier-catalogs/{cid}/groups", None),
        ("PATCH", f"/api/supplier-catalogs/{cid}", {"title": "x"}),
        ("PATCH", f"/api/supplier-catalogs/{cid}/items/{it}", {"included": False}),
        (
            "PATCH",
            f"/api/supplier-catalogs/{cid}/items/bulk",
            {"ids": [it], "changes": {"included": False}},
        ),
        ("DELETE", f"/api/supplier-catalogs/{cid}", None),
    ]:
        r = client.request(method, path, headers=other, json=body)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"
    # and the same file uploaded by the other tenant is its own catalog
    again = _upload(client, other)
    assert again.status_code == 201 and again.json()["id"] != cid


def test_routes_404_when_the_flag_is_off(client: TestClient) -> None:
    h = auth(register(client, "off2@catalog.example.com"))
    for method, path in [
        ("GET", "/api/supplier-catalogs/x"),
        ("GET", "/api/supplier-catalogs/x/items"),
        ("GET", "/api/supplier-catalogs/x/groups"),
        ("PATCH", "/api/supplier-catalogs/x/items/bulk"),
        ("DELETE", "/api/supplier-catalogs/x"),
    ]:
        assert client.request(method, path, headers=h, json={}).status_code == 404
    r = _upload(client, h)
    assert r.status_code == 404


def test_viewer_can_read_but_not_write(uploaded) -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import User

    client, h, cat = uploaded
    me = client.get("/api/auth/me", headers=h).json()
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.id == me["id"]))
        assert u is not None
        u.role = "viewer"
        s.commit()
    # token role may be cached in the JWT; log in again for a fresh one
    tok = client.post(
        "/api/auth/login", json={"email": me["email"], "password": "s3cret-pass"}
    ).json()["access_token"]
    vh = auth(tok)
    assert client.get("/api/supplier-catalogs", headers=vh).status_code == 200
    assert len(_items(client, vh, cat["id"])) > 0
    assert _upload(client, vh).status_code == 403
    it = _items(client, vh, cat["id"])[0]["id"]
    assert _patch(client, vh, cat["id"], it, {"included": False}).status_code == 403
    assert client.delete(f"/api/supplier-catalogs/{cat['id']}", headers=vh).status_code == 403


def test_storage_override_is_in_place(storage: LocalStorage) -> None:
    # guards the autouse fixture: tests must never reach R2 or /data
    provider: Callable[[], Any] = app.dependency_overrides[get_storage]
    assert provider() is storage

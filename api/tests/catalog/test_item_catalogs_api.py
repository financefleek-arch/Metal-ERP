"""Customer catalogs built from items: check, build, versions, rebuild, out of date, jobs."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import CustomerCatalog, Item, User
from app.services.catalog import item_catalogs as svc
from app.services.catalog.storage import LocalStorage
from tests.catalog.conftest import auth, register
from tests.catalog.test_api_import import CatalogEnv

URL = "/api/customer-catalogs"


def _img(color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), color).save(buf, format="JPEG")
    return buf.getvalue()


def _item(
    client: TestClient, h: dict[str, str], name: str, rate: str | None = "190", **extra: Any
) -> dict:
    body: dict[str, Any] = {"name": name, "uom": "nos", **extra}
    if rate is not None:
        body["default_rate"] = rate
    r = client.post("/api/items", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _photo(client: TestClient, h: dict[str, str], item_id: str, color=(200, 30, 30)) -> None:
    r = client.post(
        f"/api/items/{item_id}/photo",
        headers=h,
        files={"file": ("p.jpg", _img(color), "image/jpeg")},
    )
    assert r.status_code == 200, r.text


def _ids(*items: dict) -> dict:
    return {"ids": [i["id"] for i in items]}


def _check(client: TestClient, h: dict[str, str], selection: dict, **layout: Any) -> Any:
    return client.post(f"{URL}/check", headers=h, json={"selection": selection, **layout})


def _create(
    client: TestClient,
    h: dict[str, str],
    selection: dict,
    title: str = "Winter range",
    **extra: Any,
) -> Any:
    return client.post(f"{URL}", headers=h, json={"selection": selection, "title": title, **extra})


def _list(client: TestClient, h: dict[str, str]) -> list[dict]:
    r = client.get(URL, headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _file(client: TestClient, h: dict[str, str], cc_id: str) -> bytes:
    r = client.get(f"{URL}/{cc_id}/file", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    return r.content


def _text(pdf: bytes) -> str:
    return " ".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


def _age(cc_id: str, hours: int = 1) -> None:
    """Settle the clocks: the items last changed long ago and the catalog was built `hours` ago, so
    anything done to an item afterwards counts as a change. (The test database's clock only ticks
    in whole seconds, so edits made straight after a build would not register.)"""
    now = datetime.now(UTC)
    with SessionLocal() as s:
        s.execute(update(Item).values(updated_at=now - timedelta(hours=hours + 2)))
        s.execute(
            update(CustomerCatalog)
            .where(CustomerCatalog.id == cc_id)
            .values(created_at=now - timedelta(hours=hours))
        )
        s.commit()


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], list[dict]]:
    """Three items in stock, with prices and photos."""
    client, h, _ = catalog_client
    items = [
        _item(client, h, "Beer Mug 480 ML", "1256", pack_qty=6),
        _item(client, h, "Juice Glass 190 ML", "190", pack_qty=6),
        _item(client, h, "Dessert Bowl Set", "299", pack_qty=7),
    ]
    for i, it in enumerate(items):
        _photo(client, h, it["id"], (30 * i, 90, 160))
    return client, h, items


# --- check -------------------------------------------------------------------------------------


def test_check_counts_what_goes_in_and_what_is_left_out(shop) -> None:
    client, h, items = shop
    away = _item(client, h, "Sold out thing", availability="out_of_stock")
    dead = _item(client, h, "Old thing", availability="discontinued")
    soon = _item(client, h, "Coming thing", availability="expected")
    free = _item(client, h, "No price thing", rate=None)
    plain = _item(client, h, "No photo thing", "50")
    sel = _ids(*items, away, dead, soon, free, plain)

    c = _check(client, h, sel).json()
    assert c["selected"] == 8 and c["included"] == 4  # 3 + the one without a photo
    assert (c["left_out_out_of_stock"], c["left_out_discontinued"], c["left_out_expected"]) == (
        1,
        1,
        1,
    )
    assert c["no_price"] == 1
    assert c["missing_photos"] == 1
    assert c["missing_photo_items"][0]["item_id"] == plain["id"]

    wide = _check(client, h, sel, include_expected=True).json()
    assert wide["included"] == 5 and wide["left_out_expected"] == 0


def test_check_by_filter(shop) -> None:
    client, h, items = shop
    _item(client, h, "Sold out thing", availability="out_of_stock")
    c = _check(client, h, {"filter": {"availability": ["in_stock"]}}).json()
    assert c["selected"] == 3 and c["included"] == 3


def test_selection_needs_ids_or_a_filter(shop) -> None:
    client, h, items = shop
    assert _check(client, h, {}).status_code == 422
    both = {"ids": [items[0]["id"]], "filter": {}}
    assert _check(client, h, both).status_code == 422


# --- create ------------------------------------------------------------------------------------


def test_create_builds_version_one_from_the_items(shop) -> None:
    client, h, items = shop
    r = _create(client, h, _ids(*items))
    assert r.status_code == 201, r.text
    cc = r.json()
    assert cc["version"] == 1 and cc["item_count"] == 3 and cc["selection_kind"] == "ids"
    assert cc["stale"] is False and cc["latest"] is True and cc["series_id"]
    text = _text(_file(client, h, cc["id"]))
    for name in ("Beer Mug 480 ML", "Juice Glass 190 ML", "Dessert Bowl Set"):
        assert name in text
    assert "Winter range" in text
    assert {"1,256", "190", "299"} <= set(re.findall(r"Rs ([\d,]+)", text))


def test_the_pdf_uses_the_item_photo_and_pack_size(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items)).json()
    doc = pymupdf.open(stream=_file(client, h, cc["id"]), filetype="pdf")
    assert sum(len(p.get_images()) for p in doc) >= 1
    assert "for 6 pcs" in _text(_file(client, h, cc["id"]))
    per_piece = _create(client, h, _ids(*items), title="Per piece", price_basis="piece").json()
    assert "per piece" in _text(_file(client, h, per_piece["id"]))


def test_cost_price_never_reaches_the_catalog(shop) -> None:
    client, h, items = shop
    with SessionLocal() as s:
        s.get(Item, items[0]["id"]).last_purchase_rate = 987.65
        s.commit()
    cc = _create(client, h, _ids(*items)).json()
    assert "987" not in _text(_file(client, h, cc["id"]))


def test_only_in_stock_items_go_in_and_expected_ones_can_be_added_with_a_label(shop) -> None:
    client, h, items = shop
    soon = _item(client, h, "Coming Teapot", "400", availability="expected")
    _photo(client, h, soon["id"], (5, 5, 5))
    sel = _ids(*items, soon)

    plain = _create(client, h, sel).json()
    assert plain["item_count"] == 3 and "Coming Teapot" not in _text(_file(client, h, plain["id"]))

    added = _create(client, h, sel, title="With expected", include_expected=True).json()
    text = _text(_file(client, h, added["id"]))
    assert added["item_count"] == 4 and "Coming Teapot" in text and "On order" not in text

    tagged = _create(
        client, h, sel, title="Tagged", include_expected=True, label_expected=True
    ).json()
    assert "On order" in _text(_file(client, h, tagged["id"]))


def test_missing_photos_get_a_placeholder_or_block_the_build(shop) -> None:
    client, h, items = shop
    bare = _item(client, h, "Bare Plate", "75")
    sel = _ids(*items, bare)
    ok = _create(client, h, sel)
    assert ok.status_code == 201 and "No photo" in _text(_file(client, h, ok.json()["id"]))
    blocked = _create(client, h, sel, title="Strict", missing_photos="block")
    assert blocked.status_code == 422 and "no photo" in blocked.json()["detail"].lower()
    _photo(client, h, bare["id"], (1, 2, 3))
    assert _create(client, h, sel, title="Strict", missing_photos="block").status_code == 201


def test_nothing_to_include_and_too_many_items(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, items = shop
    gone = _item(client, h, "Gone", availability="out_of_stock")
    r = _create(client, h, _ids(gone))
    assert r.status_code == 422 and "Nothing to include" in r.json()["detail"]
    monkeypatch.setattr(svc, "MAX_ITEMS", 2)
    big = _create(client, h, _ids(*items))
    assert big.status_code == 422 and "limit" in big.json()["detail"]


def test_a_series_that_does_not_exist_is_404(shop) -> None:
    client, h, items = shop
    assert _create(client, h, _ids(*items), series_id="nope").status_code == 404


def test_the_cover_uses_the_firm_profile(shop) -> None:
    client, h, items = shop
    client.patch("/api/tenant", headers=h, json={"trade_name": "Sharma Bartan Bhandar"})
    cc = _create(client, h, _ids(*items), title="Diwali list").json()
    text = _text(_file(client, h, cc["id"]))
    assert "Sharma Bartan Bhandar" in text and "Diwali list" in text


def test_preview_is_a_png_and_needs_items(shop) -> None:
    client, h, items = shop
    r = client.post(f"{URL}/preview", headers=h, json={"selection": _ids(*items)})
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    gone = _item(client, h, "Gone", availability="out_of_stock")
    assert (
        client.post(f"{URL}/preview", headers=h, json={"selection": _ids(gone)}).status_code == 422
    )


# --- versions, rebuild, out of date ------------------------------------------------------------


def test_rebuild_makes_the_next_version_and_a_filter_picks_up_new_items(shop) -> None:
    client, h, items = shop
    first = _create(client, h, {"filter": {"availability": ["in_stock"]}}).json()
    assert first["selection_kind"] == "filter" and first["item_count"] == 3

    late = _item(client, h, "Late Arrival Tray", "320")
    _photo(client, h, late["id"], (9, 9, 9))
    v2 = client.post(f"{URL}/{first['id']}/rebuild", headers=h, json={})
    assert v2.status_code == 201, v2.text
    assert v2.json()["version"] == 2 and v2.json()["series_id"] == first["series_id"]
    assert v2.json()["item_count"] == 4
    assert "Late Arrival Tray" in _text(_file(client, h, v2.json()["id"]))

    listed = _list(client, h)
    assert [(r["version"], r["latest"]) for r in listed] == [(2, True), (1, False)]
    assert listed[1]["stale"] is None  # an older version is just older


def test_a_rebuild_from_ids_repeats_the_same_items(shop) -> None:
    client, h, items = shop
    first = _create(client, h, _ids(*items[:2])).json()
    _item(client, h, "Another")
    v2 = client.post(f"{URL}/{first['id']}/rebuild", headers=h, json={"title": "Renamed"}).json()
    assert v2["item_count"] == 2 and v2["title"] == "Renamed"


def test_out_of_date_follows_the_items(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items)).json()
    assert _list(client, h)[0]["stale"] is False
    _age(cc["id"])

    assert _list(client, h)[0]["stale"] is False  # nothing has changed since
    client.patch(f"/api/items/{items[0]['id']}", headers=h, json={"default_rate": "1300"})
    assert _list(client, h)[0]["stale"] is True

    fresh = client.post(f"{URL}/{cc['id']}/rebuild", headers=h, json={}).json()
    assert _list(client, h)[0]["id"] == fresh["id"] and _list(client, h)[0]["stale"] is False


def test_a_photo_change_makes_it_out_of_date(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items)).json()
    _age(cc["id"])
    _photo(client, h, items[1]["id"], (123, 45, 67))
    assert _list(client, h)[0]["stale"] is True


def test_a_deleted_or_archived_item_makes_it_out_of_date(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items)).json()
    _age(cc["id"])
    assert client.delete(f"/api/items/{items[2]['id']}", headers=h).status_code == 204
    assert _list(client, h)[0]["stale"] is True


def test_a_filter_catalog_is_out_of_date_when_membership_changes(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, {"filter": {"availability": ["in_stock"]}}).json()
    _age(cc["id"])
    assert _list(client, h)[0]["stale"] is False
    new = _item(client, h, "Brand new", "88")
    assert new and _list(client, h)[0]["stale"] is True


def test_an_item_selling_out_makes_it_out_of_date(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items)).json()
    _age(cc["id"])
    client.patch(f"/api/items/{items[0]['id']}", headers=h, json={"availability": "out_of_stock"})
    assert _list(client, h)[0]["stale"] is True


# --- background jobs ---------------------------------------------------------------------------


def test_big_catalogs_run_as_a_job_that_creates_a_version(
    shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, items = shop
    monkeypatch.setattr(svc, "SYNC_ITEM_LIMIT", 1)
    r = _create(client, h, _ids(*items))
    assert r.status_code == 202, r.text
    job = client.get(f"{URL}/jobs/{r.json()['id']}", headers=h).json()
    assert job["status"] == "done" and job["total"] == 3 and job["result_id"]
    assert _list(client, h)[0]["id"] == job["result_id"]
    assert len(_file(client, h, job["result_id"])) > 1000


def test_job_failure_is_reported_on_the_job(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, items = shop
    monkeypatch.setattr(svc, "SYNC_ITEM_LIMIT", 1)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(svc, "render_pdf", boom)
    r = _create(client, h, _ids(*items))
    job = client.get(f"{URL}/jobs/{r.json()['id']}", headers=h).json()
    assert job["status"] == "error" and "renderer exploded" in job["error"]
    assert _list(client, h) == []


def test_a_lost_photo_does_not_sink_the_catalog(shop, storage: LocalStorage) -> None:
    client, h, items = shop
    with SessionLocal() as s:
        from app.models import MediaAsset

        asset = s.get(MediaAsset, s.get(Item, items[0]["id"]).primary_media_id)
        storage.delete(asset.key)
    r = _create(client, h, _ids(*items))
    assert r.status_code == 201


# --- files, deleting, isolation, permissions ---------------------------------------------------


def test_download_has_a_versioned_name_and_delete_removes_the_file(
    shop, storage: LocalStorage
) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items), title="Winter range").json()
    r = client.get(f"{URL}/{cc['id']}/file", headers=h)
    assert "catalog-winter-range-v1.pdf" in r.headers["content-disposition"]
    with SessionLocal() as s:
        key = s.get(CustomerCatalog, cc["id"]).pdf_key
    assert storage.get(key)
    assert client.delete(f"{URL}/{cc['id']}", headers=h).status_code == 204
    assert client.get(f"{URL}/{cc['id']}/file", headers=h).status_code == 404
    with pytest.raises(Exception):  # noqa: B017 - the object is gone
        storage.get(key)


def test_another_firm_cannot_see_or_touch_them(shop, client: TestClient) -> None:
    c, h, items = shop
    cc = _create(c, h, _ids(*items)).json()
    other = auth(register(client, "other6@catalog.example.com"))
    with SessionLocal() as s:  # give the other firm the module too
        from app.models import Tenant

        tid = s.scalar(select(User.tenant_id).where(User.email == "other6@catalog.example.com"))
        s.get(Tenant, tid).ext_supplier_catalog = True
        s.commit()
    assert _list(client, other) == []
    assert client.get(f"{URL}/{cc['id']}/file", headers=other).status_code == 404
    assert client.delete(f"{URL}/{cc['id']}", headers=other).status_code == 404
    assert client.post(f"{URL}/{cc['id']}/rebuild", headers=other, json={}).status_code == 404
    mine = _item(client, other, "Theirs", "10")
    stolen = _create(client, other, _ids(items[0], mine))  # someone else's id is simply not there
    assert stolen.status_code == 201 and stolen.json()["item_count"] == 1


def test_flag_off_hides_the_routes(client: TestClient) -> None:
    h = auth(register(client, "off5@catalog.example.com"))
    for method, path in [
        ("POST", f"{URL}/check"),
        ("POST", f"{URL}"),
        ("POST", f"{URL}/preview"),
        ("GET", f"{URL}"),
        ("GET", f"{URL}/y/file"),
        ("DELETE", f"{URL}/y"),
        ("POST", f"{URL}/y/rebuild"),
    ]:
        assert client.request(method, path, headers=h, json={}).status_code == 404


def test_viewers_can_read_but_not_create_or_delete(shop) -> None:
    client, h, items = shop
    cc = _create(client, h, _ids(*items)).json()
    me = client.get("/api/auth/me", headers=h).json()
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.id == me["id"]))
        assert u is not None
        u.role = "viewer"
        s.commit()
    tok = client.post("/api/auth/login", json={"email": me["email"], "password": "s3cret-pass"})
    vh = auth(tok.json()["access_token"])
    assert len(_list(client, vh)) == 1
    assert len(_file(client, vh, cc["id"])) > 1000
    assert _check(client, vh, _ids(*items)).status_code == 200
    assert _create(client, vh, _ids(*items)).status_code == 403
    assert client.delete(f"{URL}/{cc['id']}", headers=vh).status_code == 403
    assert client.post(f"{URL}/{cc['id']}/rebuild", headers=vh, json={}).status_code == 403

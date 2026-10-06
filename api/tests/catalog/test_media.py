"""Item photos: normalise, dedupe, attach, bulk attach, serve, usage, sweep, catalog copy."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item, MediaAsset
from app.services import media as media_svc
from app.services.catalog.storage import LocalStorage
from tests.catalog.conftest import auth, register
from tests.catalog.pdfs import TestCell, make_pdf
from tests.catalog.test_api_import import CatalogEnv, _items, _upload


def _img(w: int = 3000, h: int = 2000, fmt: str = "JPEG", color=(200, 30, 30), **kw: Any) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format=fmt, **kw)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _item(
    client: TestClient, h: dict[str, str], name: str = "Steel Plate 10 In", **extra: Any
) -> dict:
    r = client.post("/api/items", headers=h, json={"name": name, "uom": "nos", **extra})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _upload_photo(
    client: TestClient, h: dict[str, str], item_id: str, data: bytes, name="p.jpg"
) -> Any:
    return client.post(
        f"/api/items/{item_id}/photo", headers=h, files={"file": (name, data, "image/jpeg")}
    )


# --- normalise ---------------------------------------------------------------------------


def test_a_large_photo_is_shrunk_to_webp_with_a_thumbnail() -> None:
    raw = _img(3000, 2000)
    r = media_svc.normalise(raw)
    main, thumb = _open(r.main), _open(r.thumb)
    assert main.format == thumb.format == "WEBP"
    assert max(main.size) == media_svc.MAIN_EDGE and (r.width, r.height) == main.size
    assert max(thumb.size) == media_svc.THUMB_EDGE
    assert len(r.main) < len(raw) / 3 and len(r.thumb) < len(r.main)


def test_a_small_photo_is_not_enlarged() -> None:
    r = media_svc.normalise(_img(300, 200))
    assert (r.width, r.height) == (300, 200)


def test_exif_rotation_is_applied_and_metadata_dropped() -> None:
    im = Image.new("RGB", (200, 100), (10, 120, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to view
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    r = media_svc.normalise(buf.getvalue())
    assert (r.width, r.height) == (100, 200)
    assert not _open(r.main).getexif()


def test_transparency_is_flattened_onto_white() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(buf, format="PNG")
    out = _open(media_svc.normalise(buf.getvalue()).main).convert("RGB")
    assert out.getpixel((10, 10)) == (255, 255, 255)


@pytest.mark.parametrize(
    "data,why",
    [
        (b"", "empty"),
        (b"not an image at all", "not a photo"),
        (_img(40, 40, "GIF"), "JPG, PNG or WebP"),
        (_img(40, 40, "BMP"), "JPG, PNG or WebP"),
    ],
)
def test_bad_uploads_are_rejected_with_a_clear_message(data: bytes, why: str) -> None:
    with pytest.raises(media_svc.ImageRejected) as e:
        media_svc.normalise(data)
    assert why.split()[0].lower() in str(e.value).lower()


def test_size_and_pixel_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_svc, "MAX_UPLOAD_BYTES", 1000)
    with pytest.raises(media_svc.ImageRejected, match="over"):
        media_svc.normalise(_img(2000, 2000, quality=95))
    monkeypatch.setattr(media_svc, "MAX_UPLOAD_BYTES", 10_000_000)
    monkeypatch.setattr(media_svc, "MAX_PIXELS", 1000)
    with pytest.raises(media_svc.ImageRejected, match="too large"):
        media_svc.normalise(_img(100, 100))


# --- one item ------------------------------------------------------------------------------


def test_attach_a_photo_and_see_it_on_the_item(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    item = _item(client, h)
    assert item["photo_url"] is None and item["thumb_url"] is None

    r = _upload_photo(client, h, item["id"], _img())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["photo_url"].startswith("/api/media/") and out["thumb_url"].endswith(
        tuple("0123456789abcdef")
    )

    got = client.get(f"/api/items/{item['id']}", headers=h).json()
    assert got["photo_url"] and got["thumb_url"]
    listed = next(i for i in client.get("/api/items", headers=h).json() if i["id"] == item["id"])
    assert listed["thumb_url"]

    img = client.get(out["thumb_url"])  # signed link: no bearer header needed
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp"
    assert max(_open(img.content).size) == media_svc.THUMB_EDGE
    full = client.get(out["photo_url"])
    assert max(_open(full.content).size) == media_svc.MAIN_EDGE


def test_a_tampered_or_unknown_link_is_refused(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    item = _item(client, h)
    url = _upload_photo(client, h, item["id"], _img()).json()["photo_url"]
    assert client.get(url[:-3] + "000").status_code == 403
    assert client.get(url.replace("/photo?", "/other?")).status_code == 404


def test_replace_and_remove(catalog_client: CatalogEnv, storage: LocalStorage) -> None:
    client, h, tid = catalog_client
    item = _item(client, h)
    first = _upload_photo(client, h, item["id"], _img(color=(1, 2, 3))).json()["photo_url"]
    second = _upload_photo(client, h, item["id"], _img(color=(250, 250, 0))).json()["photo_url"]
    assert first.split("?")[0] != second.split("?")[0]
    assert client.delete(f"/api/items/{item['id']}/photo", headers=h).status_code == 204
    assert client.get(f"/api/items/{item['id']}", headers=h).json()["photo_url"] is None
    with SessionLocal() as s:  # the images stay until nothing refers to them
        assert s.query(MediaAsset).filter_by(tenant_id=tid).count() == 2


def test_the_same_picture_is_stored_once_per_firm(catalog_client: CatalogEnv) -> None:
    client, h, tid = catalog_client
    a, b = _item(client, h, "Plate A"), _item(client, h, "Plate B")
    ua = _upload_photo(client, h, a["id"], _img()).json()["photo_url"].split("?")[0]
    ub = _upload_photo(client, h, b["id"], _img()).json()["photo_url"].split("?")[0]
    assert ua == ub
    with SessionLocal() as s:
        assert s.query(MediaAsset).filter_by(tenant_id=tid).count() == 1


def test_bad_and_oversize_uploads_over_http(
    catalog_client: CatalogEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, _ = catalog_client
    item = _item(client, h)
    r = _upload_photo(client, h, item["id"], b"definitely not an image")
    assert r.status_code == 422 and "photo" in r.json()["detail"].lower()
    monkeypatch.setattr(media_svc, "MAX_UPLOAD_BYTES", 500)
    assert _upload_photo(client, h, item["id"], _img(800, 800, quality=95)).status_code == 413


def test_another_firms_item_is_not_found(catalog_client: CatalogEnv, client: TestClient) -> None:
    c, h, _ = catalog_client
    other = auth(register(client, "other3@catalog.example.com"))
    foreign = _item(client, other, "Foreign Item")
    assert _upload_photo(c, h, foreign["id"], _img()).status_code == 404
    assert c.delete(f"/api/items/{foreign['id']}/photo", headers=h).status_code == 404


# --- many items ----------------------------------------------------------------------------


def test_bulk_photos_match_by_code_then_name_and_apply(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    a = _item(client, h, "Beer Mug 480 ML", sku="100234")
    b = _item(client, h, "Juice Glass 190 ML")
    files = [
        ("files", ("100234.jpg", _img(600, 400), "image/jpeg")),
        ("files", ("Juice Glass 190 ML.jpg", _img(600, 400, color=(0, 9, 90)), "image/jpeg")),
        ("files", ("100234_side.png", _img(600, 400, "PNG", color=(5, 5, 5)), "image/png")),
        ("files", ("mystery.jpg", _img(600, 400, color=(70, 70, 70)), "image/jpeg")),
        ("files", ("broken.jpg", b"nope", "image/jpeg")),
    ]
    staged = client.post("/api/items/photos/stage", headers=h, files=files).json()
    by_name = {s["file_name"]: s for s in staged}
    assert by_name["100234.jpg"]["match"]["item_id"] == a["id"]
    assert by_name["100234.jpg"]["match"]["method"] == "code"
    assert by_name["100234_side.png"]["match"]["item_id"] == a["id"]  # first word is the code
    assert by_name["Juice Glass 190 ML.jpg"]["match"] == {
        "item_id": b["id"],
        "name": "Juice Glass 190 ML",
        "code": None,
        "method": "name",
    }
    assert by_name["mystery.jpg"]["match"] is None and by_name["mystery.jpg"]["media_id"]
    assert by_name["broken.jpg"]["error"] and by_name["broken.jpg"]["media_id"] is None

    # nothing is attached until applied
    assert client.get(f"/api/items/{a['id']}", headers=h).json()["photo_url"] is None
    pairs = [
        {"media_id": by_name["100234.jpg"]["media_id"], "item_id": a["id"]},
        {"media_id": by_name["Juice Glass 190 ML.jpg"]["media_id"], "item_id": b["id"]},
        {"media_id": by_name["mystery.jpg"]["media_id"], "item_id": "no-such-item"},
    ]
    out = client.post("/api/items/photos/apply", headers=h, json={"pairs": pairs}).json()
    assert out == {"applied": 2, "skipped": 1}
    assert client.get(f"/api/items/{a['id']}", headers=h).json()["photo_url"]
    assert client.get(f"/api/items/{b['id']}", headers=h).json()["photo_url"]


def test_apply_refuses_another_firms_photo(catalog_client: CatalogEnv, client: TestClient) -> None:
    c, h, _ = catalog_client
    other = auth(register(client, "other4@catalog.example.com"))
    foreign_item = _item(client, other, "Foreign Item")
    staged = client.post(
        "/api/items/photos/stage",
        headers=other,
        files=[("files", ("a.jpg", _img(400, 300), "image/jpeg"))],
    ).json()
    mine = _item(c, h)
    out = c.post(
        "/api/items/photos/apply",
        headers=h,
        json={"pairs": [{"media_id": staged[0]["media_id"], "item_id": mine["id"]}]},
    ).json()
    assert out == {"applied": 0, "skipped": 1}
    assert foreign_item  # (the other firm's item was never touched)


def test_bulk_stage_is_capped(catalog_client: CatalogEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.media as media_router

    client, h, _ = catalog_client
    monkeypatch.setattr(media_router, "_MAX_BULK", 2)
    files = [("files", (f"{i}.jpg", _img(60, 60), "image/jpeg")) for i in range(3)]
    assert client.post("/api/items/photos/stage", headers=h, files=files).status_code == 422


# --- usage and cleanup ---------------------------------------------------------------------


def test_usage_counts_images_and_bytes(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    assert client.get("/api/media/usage", headers=h).json() == {"images": 0, "bytes": 0}
    item = _item(client, h)
    _upload_photo(client, h, item["id"], _img())
    u = client.get("/api/media/usage", headers=h).json()
    assert u["images"] == 1 and 0 < u["bytes"] < 200_000


def test_sweep_removes_only_unreferenced_old_images(
    catalog_client: CatalogEnv, storage: LocalStorage
) -> None:
    client, h, tid = catalog_client
    keep = _item(client, h, "Keeps its photo")
    gone = _item(client, h, "Loses its photo")
    _upload_photo(client, h, keep["id"], _img(color=(9, 9, 9)))
    _upload_photo(client, h, gone["id"], _img(color=(99, 9, 9)))
    client.delete(f"/api/items/{gone['id']}/photo", headers=h)
    with SessionLocal() as s:
        assets = s.scalars(select(MediaAsset).where(MediaAsset.tenant_id == tid)).all()
        assert len(assets) == 2
        # inside the grace period nothing goes
        assert media_svc.sweep_orphans(s, storage, tenant_id=tid) == (0, 0)
        future = datetime.now(UTC) + timedelta(days=3)
        removed, freed = media_svc.sweep_orphans(s, storage, tenant_id=tid, now=future)
        s.commit()
        assert removed == 1 and freed > 0
        left = s.scalars(select(MediaAsset).where(MediaAsset.tenant_id == tid)).all()
        assert len(left) == 1
        assert storage.get(left[0].key) and storage.get(left[0].thumb_key)  # still stored


# --- promote copies the catalog photo --------------------------------------------------------


def _cells(*codes: str) -> bytes:
    return make_pdf(
        [[TestCell(code=c, name_lines=[f"BEER MUG {c} ML", "COL BOX 6 PC"]) for c in codes]]
    )


def test_promote_copies_the_catalog_photo_onto_the_item(catalog_client: CatalogEnv) -> None:
    client, h, tid = catalog_client
    cat = _upload(client, h, data=_cells("11", "12"), name="a.pdf").json()
    rows = _items(client, h, cat["id"])
    r = client.post(
        f"/api/supplier-catalogs/{cat['id']}/promote", headers=h, json={"all_included": True}
    )
    assert r.status_code == 200 and r.json()["photos_queued"] == 2
    items = {i["id"]: i for i in client.get("/api/items", headers=h).json()}
    for row in _items(client, h, cat["id"]):
        assert row["item_id"] and items[row["item_id"]]["thumb_url"], "photo was not copied"
    assert len(rows) == 2
    with SessionLocal() as s:  # the two cells share the default photo: stored once
        assert s.query(MediaAsset).filter_by(tenant_id=tid).count() == 1

    again = client.post(
        f"/api/supplier-catalogs/{cat['id']}/promote", headers=h, json={"all_included": True}
    ).json()
    assert again["photos_queued"] == 0


def test_an_items_own_photo_is_never_replaced_by_a_catalog_photo(
    catalog_client: CatalogEnv,
) -> None:
    client, h, _ = catalog_client
    cat = _upload(client, h, data=_cells("21"), name="a.pdf").json()
    row = _items(client, h, cat["id"])[0]
    made = _item(client, h, row["display_name"])  # promote will link to this existing item
    own = (
        _upload_photo(client, h, made["id"], _img(color=(1, 1, 1)))
        .json()["photo_url"]
        .split("?")[0]
    )
    out = client.post(
        f"/api/supplier-catalogs/{cat['id']}/promote", headers=h, json={"all_included": True}
    ).json()
    assert out["link_existing"] == 1 and out["photos_queued"] == 0
    after = client.get(f"/api/items/{made['id']}", headers=h).json()["photo_url"].split("?")[0]
    assert after == own


def test_the_item_model_has_the_stock_flag_default_true(catalog_client: CatalogEnv) -> None:
    client, h, _ = catalog_client
    item = _item(client, h)
    with SessionLocal() as s:
        assert s.get(Item, item["id"]).is_stock is True

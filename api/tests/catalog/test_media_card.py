"""The WhatsApp card picture: a square JPEG from the item's stored photo, on a long-lived signed link."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaAsset
from app.services import media as media_svc
from app.services.catalog import image_url
from app.services.media_urls import SCOPE, card_url
from tests.catalog.conftest import auth, register
from tests.catalog.test_media import _img, _item, _open, _upload_photo


def _asset_of(client: TestClient, h: dict[str, str], photo: bytes) -> str:
    item = _item(client, h)
    assert _upload_photo(client, h, item["id"], photo).status_code == 200
    with SessionLocal() as s:
        asset = s.query(MediaAsset).one()
        return asset.id


def test_a_wide_photo_becomes_a_square_jpeg_with_nothing_cropped(catalog_client, _local_storage) -> None:  # type: ignore[no-untyped-def]
    client, h, _ = catalog_client
    asset_id = _asset_of(client, h, _img(3000, 1500, color=(200, 30, 30)))
    with SessionLocal() as s:
        asset = s.get(MediaAsset, asset_id)
        data = media_svc.card_jpeg(_local_storage, asset)  # type: ignore[arg-type]
    assert data is not None
    im = _open(data)
    assert im.format == "JPEG" and im.size == (media_svc.CARD_EDGE, media_svc.CARD_EDGE)
    assert (
        im.getpixel((400, 5)) == (255, 255, 255) or sum(im.getpixel((400, 5))) > 740
    )  # white above the wide picture
    assert (
        im.getpixel((400, 400))[0] > 150 and im.getpixel((400, 400))[1] < 90
    )  # the picture is in the middle
    assert len(data) < 5 * 1024 * 1024  # WhatsApp's limit


def test_the_card_is_made_once_and_kept(catalog_client, _local_storage) -> None:  # type: ignore[no-untyped-def]
    client, h, _ = catalog_client
    asset_id = _asset_of(client, h, _img(800, 800))
    with SessionLocal() as s:
        asset = s.get(MediaAsset, asset_id)
        storage = _local_storage
        first = media_svc.card_jpeg(storage, asset)  # type: ignore[arg-type]
        key = media_svc.card_key(asset.tenant_id, asset.sha256)  # type: ignore[union-attr]
        assert storage.get_many([key]).get(key) == first
        assert media_svc.card_jpeg(storage, asset) == first  # type: ignore[arg-type]


def test_the_card_link_is_absolute_serves_a_jpeg_and_outlives_a_page_link(
    catalog_client, _local_storage
) -> None:  # type: ignore[no-untyped-def]
    client, h, _ = catalog_client
    asset_id = _asset_of(client, h, _img(1200, 900))
    url = card_url(asset_id)
    assert url.startswith("http") and f"/api/media/{asset_id}/card?" in url
    path = url[url.index("/api/") :]
    r = client.get(path)  # no bearer header: the signature is the key
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert _open(r.content).size == (media_svc.CARD_EDGE, media_svc.CARD_EDGE)
    exp = int(path.split("e=")[1].split("&")[0])
    assert exp - time.time() > 13 * 24 * 3600  # a paced send over hours or days still finds the picture
    page = image_url.sign_query(asset_id, scope=SCOPE)
    assert (
        int(page.split("e=")[1].split("&")[0]) - time.time() < 3 * 3600
    )  # the page's own links are unchanged


def test_a_tampered_card_link_is_refused_and_a_firm_without_a_photo_has_no_card(
    catalog_client, _local_storage
) -> None:  # type: ignore[no-untyped-def]
    client, h, _ = catalog_client
    asset_id = _asset_of(client, h, _img(400, 400))
    path = card_url(asset_id)[card_url(asset_id).index("/api/") :]
    assert client.get(path[:-3] + "000").status_code == 403
    assert client.get(path.replace(asset_id, "nope")).status_code in (403, 404)
    other = auth(register(client, "other-firm@catalog.example.com"))
    assert client.get("/api/media/usage", headers=other).json() == {"images": 0, "bytes": 0}

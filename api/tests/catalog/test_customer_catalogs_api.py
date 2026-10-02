"""S4 API: customer catalog PDFs, versions, staleness, background jobs, cleanup, isolation."""

from __future__ import annotations

import io
import re
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.services.catalog import customer_catalogs as cc_svc
from app.services.catalog.storage import LocalStorage
from tests.catalog.conftest import SAMPLE_PDF, auth, enable_catalog_flag, register

Env = tuple[TestClient, dict[str, str], str]


@pytest.fixture
def uploaded(catalog_client: Env) -> tuple[TestClient, dict[str, str], dict[str, Any]]:
    client, h, _ = catalog_client
    r = client.post(
        "/api/supplier-catalogs",
        headers=h,
        files={"file": ("glassware.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return client, h, r.json()


def _items(client: TestClient, h: dict[str, str], cid: str) -> list[dict[str, Any]]:
    r = client.get(f"/api/supplier-catalogs/{cid}/items", headers=h, params={"limit": 200})
    return r.json()  # type: ignore[no-any-return]


def _make(
    client: TestClient, h: dict[str, str], cid: str, body: dict[str, Any] | None = None
) -> Any:
    return client.post(
        f"/api/supplier-catalogs/{cid}/customer-catalogs",
        headers=h,
        json={"all_included": True, **(body or {})},
    )


def _list(client: TestClient, h: dict[str, str], cid: str) -> list[dict[str, Any]]:
    r = client.get(f"/api/supplier-catalogs/{cid}/customer-catalogs", headers=h)
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def _file(client: TestClient, h: dict[str, str], cid: str, ccid: str) -> bytes:
    r = client.get(f"/api/supplier-catalogs/{cid}/customer-catalogs/{ccid}/file", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    return r.content


def _text(pdf: bytes) -> str:
    return " ".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


def _prices(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"Rs ([\d,]+(?:\.\d+)?)", text)}


# --- create -----------------------------------------------------------------------


def test_create_builds_version_one(uploaded) -> None:
    client, h, cat = uploaded
    r = _make(client, h, cat["id"])
    assert r.status_code == 201, r.text
    cc = r.json()
    assert cc["version"] == 1 and cc["stale"] is False
    assert cc["item_count"] == 24 and cc["page_count"] >= 3
    assert cc["title"] == "glassware" and cc["byte_size"] > 10_000
    assert cc["options"] == {
        "columns": 3, "group_by": "group", "show_code": True, "price_basis": "pack",
        "contents": True,
    }
    pdf = _file(client, h, cat["id"], cc["id"])
    assert len(pymupdf.open(stream=pdf, filetype="pdf")) == cc["page_count"]
    assert len(pdf) == cc["byte_size"]


def test_file_download_has_a_versioned_name(uploaded) -> None:
    client, h, cat = uploaded
    cc = _make(client, h, cat["id"]).json()
    r = client.get(f"/api/supplier-catalogs/{cat['id']}/customer-catalogs/{cc['id']}/file",
                   headers=h)
    assert r.headers["content-disposition"] == 'attachment; filename="catalog-glassware-v1.pdf"'


def test_the_pdf_shows_our_prices_and_never_the_cost_or_the_supplier(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    client.patch(f"/api/supplier-catalogs/{cid}", headers=h, json={"multiplier": "1.25"})
    items = _items(client, h, cid)
    cc = _make(client, h, cid).json()
    text = _text(_file(client, h, cid, cc["id"]))

    sells = {i["sell_price"].rstrip("0").rstrip(".") for i in items}
    printed = _prices(text)
    assert printed == {s for s in sells if s}  # exactly our new prices, nothing else
    costs_only = {i["cost_price"].rstrip("0").rstrip(".") for i in items} - sells
    assert costs_only  # the test is meaningful: costs differ from sell prices
    assert not (printed & costs_only)
    for i in items:
        assert i["supplier_code"] not in text, i["supplier_code"]
    assert "supplier" not in text.lower()
    assert all(i["code"] in text for i in items)  # our codes are shown by default


def test_options_reach_the_pdf(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    base = _text(_file(client, h, cid, _make(client, h, cid).json()["id"]))
    no_code = _make(client, h, cid, {"show_code": False}).json()
    assert "GL-000001" not in _text(_file(client, h, cid, no_code["id"]))
    assert "GL-000001" in base

    piece = _make(client, h, cid, {"price_basis": "piece"}).json()
    ptext = _text(_file(client, h, cid, piece["id"]))
    assert "per piece" in ptext and "for 7 pcs" not in ptext

    flat = _make(client, h, cid, {"group_by": "none"}).json()
    assert "Contents" not in _text(_file(client, h, cid, flat["id"]))
    assert "Contents" in base

    two = _make(client, h, cid, {"columns": 2}).json()
    four = _make(client, h, cid, {"columns": 4}).json()
    assert two["page_count"] > four["page_count"]


def test_cover_uses_the_firm_profile_and_a_custom_title(uploaded) -> None:
    client, h, cat = uploaded
    r = client.patch("/api/tenant", headers=h, json={
        "phone": "9876543210", "city": "Siliguri", "address": "12 Market Road",
        "pincode": "734005",
    })
    assert r.status_code == 200, r.text
    cc = _make(client, h, cat["id"], {"title": "Diwali Rates 2026"}).json()
    assert cc["title"] == "Diwali Rates 2026"
    cover = pymupdf.open(stream=_file(client, h, cat["id"], cc["id"]), filetype="pdf")[0].get_text()
    assert "SAMPLE TRADERS" in cover and "Diwali Rates 2026" in cover
    assert "12 Market Road, Siliguri, 734005" in cover and "9876543210" in cover


# --- selection ----------------------------------------------------------------------


def test_selection_modes(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    items = _items(client, h, cid)
    client.patch(f"/api/supplier-catalogs/{cid}/items/{items[0]['id']}", headers=h,
                 json={"included": False})
    assert _make(client, h, cid).json()["item_count"] == 23  # all_included skips excluded

    one = client.post(f"/api/supplier-catalogs/{cid}/customer-catalogs", headers=h,
                      json={"ids": [items[0]["id"]]}).json()
    assert one["item_count"] == 1  # explicit ids include excluded items

    n = len(client.get(f"/api/supplier-catalogs/{cid}/items", headers=h,
                       params={"q": "beer", "limit": 200}).json())
    f = client.post(f"/api/supplier-catalogs/{cid}/customer-catalogs", headers=h,
                    json={"filter": {"q": "beer"}}).json()
    assert f["item_count"] == n


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ids": ["x"], "all_included": True},
        {"all_included": True, "columns": 5},
        {"all_included": True, "group_by": "colour"},
        {"all_included": True, "price_basis": "dozen"},
        {"all_included": True, "title": ""},
    ],
)
def test_bad_requests_are_422(uploaded, body: dict[str, Any]) -> None:
    client, h, cat = uploaded
    r = client.post(f"/api/supplier-catalogs/{cat['id']}/customer-catalogs", headers=h, json=body)
    assert r.status_code == 422


def test_nothing_to_include_and_too_many_items(
    uploaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, cat = uploaded
    r = client.post(f"/api/supplier-catalogs/{cat['id']}/customer-catalogs", headers=h,
                    json={"filter": {"q": "zzzzzz"}})
    assert r.status_code == 422 and "No items" in r.json()["detail"]
    monkeypatch.setattr(cc_svc, "MAX_ITEMS", 10)
    r = _make(client, h, cat["id"])
    assert r.status_code == 422 and "limit is 10" in r.json()["detail"]
    assert _list(client, h, cat["id"]) == []  # nothing half-created


# --- versions + staleness --------------------------------------------------------------


def test_versions_count_up_and_list_newest_first(uploaded) -> None:
    client, h, cat = uploaded
    a = _make(client, h, cat["id"]).json()
    b = _make(client, h, cat["id"]).json()
    c = _make(client, h, cat["id"]).json()
    assert [a["version"], b["version"], c["version"]] == [1, 2, 3]
    assert [x["version"] for x in _list(client, h, cat["id"])] == [3, 2, 1]


def test_pricing_changes_make_every_version_stale_until_regenerated(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    v1 = _make(client, h, cid).json()
    assert not any(x["stale"] for x in _list(client, h, cid))
    client.patch(f"/api/supplier-catalogs/{cid}", headers=h, json={"multiplier": "1.3"})
    assert [x["stale"] for x in _list(client, h, cid)] == [True]
    v2 = _make(client, h, cid).json()
    states = {x["version"]: x["stale"] for x in _list(client, h, cid)}
    assert states == {v1["version"]: True, v2["version"]: False}


@pytest.mark.parametrize(
    ("body", "stale"),
    [
        ({"display_name": "Renamed item"}, True),
        ({"cost_price": "250"}, True),
        ({"pack_qty": 4}, True),
        ({"included": False}, True),
        ({"group_name": "New Group"}, True),
        ({"multiplier_override": "1.9"}, True),
        ({"brand": "Other"}, False),  # not printed on the customer catalog
        ({"size_text": "9 ML"}, False),
    ],
)
def test_item_edits_that_change_the_catalog_make_it_stale(
    uploaded, body: dict[str, Any], stale: bool
) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    _make(client, h, cid)
    item = _items(client, h, cid)[0]
    client.patch(f"/api/supplier-catalogs/{cid}/items/{item['id']}", headers=h, json=body)
    assert _list(client, h, cid)[0]["stale"] is stale


def test_bulk_edits_and_overrides_make_it_stale(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    _make(client, h, cid)
    ids = [i["id"] for i in _items(client, h, cid)[:2]]
    client.patch(f"/api/supplier-catalogs/{cid}/items/bulk", headers=h,
                 json={"ids": ids, "changes": {"multiplier_override": "1.5"}})
    assert _list(client, h, cid)[0]["stale"] is True


def test_a_bulk_edit_that_touches_nothing_leaves_it_current(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    _make(client, h, cid)
    client.patch(f"/api/supplier-catalogs/{cid}/items/bulk", headers=h,
                 json={"ids": ["no-such-item"], "changes": {"included": False}})
    assert _list(client, h, cid)[0]["stale"] is False


def test_renaming_the_catalog_does_not_make_it_stale(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    _make(client, h, cid)
    client.patch(f"/api/supplier-catalogs/{cid}", headers=h, json={"title": "Renamed"})
    assert _list(client, h, cid)[0]["stale"] is False


# --- background job ------------------------------------------------------------------


@pytest.fixture
def small_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc_svc, "SYNC_ITEM_LIMIT", 5)


def test_big_catalogs_run_as_a_job_that_creates_a_version(uploaded, small_sync) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    r = _make(client, h, cid)
    assert r.status_code == 202
    job = client.get(f"/api/supplier-catalogs/{cid}/outputs/{r.json()['id']}", headers=h).json()
    assert job["status"] == "done" and job["progress"] == job["total"] == 24
    assert job["result_id"] and job["page_count"] >= 3
    versions = _list(client, h, cid)
    assert [v["id"] for v in versions] == [job["result_id"]]
    assert versions[0]["version"] == 1 and versions[0]["item_count"] == 24
    text = _text(_file(client, h, cid, job["result_id"]))
    assert len(_prices(text)) > 5


def test_job_failure_is_reported_on_the_job(
    uploaded, small_sync, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, cat = uploaded

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(cc_svc, "render_pdf", boom)
    r = _make(client, h, cat["id"])
    url = f"/api/supplier-catalogs/{cat['id']}/outputs/{r.json()['id']}"
    job = client.get(url, headers=h).json()
    assert job["status"] == "error" and "renderer exploded" in job["error"]
    assert job["result_id"] is None
    assert _list(client, h, cat["id"]) == []


def test_a_lost_photo_does_not_sink_the_catalog(uploaded, storage: LocalStorage) -> None:
    from app.db import SessionLocal
    from app.models import SupplierCatalogItem

    client, h, cat = uploaded
    with SessionLocal() as s:
        key = s.query(SupplierCatalogItem.image_key).filter_by(catalog_id=cat["id"]).first()[0]
    storage.delete(key)
    r = _make(client, h, cat["id"])
    assert r.status_code == 201
    assert "No photo" in _text(_file(client, h, cat["id"], r.json()["id"]))


# --- preview ----------------------------------------------------------------------------


def test_preview_is_a_png(uploaded) -> None:
    client, h, cat = uploaded
    r = client.post(f"/api/supplier-catalogs/{cat['id']}/customer-catalogs/preview", headers=h,
                    json={"all_included": True, "columns": 2})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    w, hgt = Image.open(io.BytesIO(r.content)).size
    assert 0.69 < w / hgt < 0.72
    empty = client.post(f"/api/supplier-catalogs/{cat['id']}/customer-catalogs/preview",
                        headers=h, json={"filter": {"q": "zzzzzz"}})
    assert empty.status_code == 422
    assert _list(client, h, cat["id"]) == []  # a preview stores nothing


# --- delete + cleanup --------------------------------------------------------------------


def test_deleting_a_version_removes_its_file(uploaded, storage: LocalStorage) -> None:
    from app.db import SessionLocal
    from app.models import CustomerCatalog

    client, h, cat = uploaded
    cid = cat["id"]
    a = _make(client, h, cid).json()
    b = _make(client, h, cid).json()
    with SessionLocal() as s:
        key_a = s.get(CustomerCatalog, a["id"]).pdf_key  # type: ignore[union-attr]
    assert storage.get(key_a)
    r = client.delete(f"/api/supplier-catalogs/{cid}/customer-catalogs/{a['id']}", headers=h)
    assert r.status_code == 204
    assert [v["id"] for v in _list(client, h, cid)] == [b["id"]]
    with pytest.raises(FileNotFoundError):
        storage.get(key_a)
    assert client.delete(f"/api/supplier-catalogs/{cid}/customer-catalogs/{a['id']}",
                         headers=h).status_code == 404
    # version numbers are not reused within a catalog while newer ones exist
    assert _make(client, h, cid).json()["version"] == 3


def test_deleting_the_supplier_catalog_removes_customer_catalogs_and_files(
    uploaded, storage: LocalStorage
) -> None:
    from app.db import SessionLocal
    from app.models import CustomerCatalog

    client, h, cat = uploaded
    cc = _make(client, h, cat["id"]).json()
    with SessionLocal() as s:
        key = s.get(CustomerCatalog, cc["id"]).pdf_key  # type: ignore[union-attr]
    assert client.delete(f"/api/supplier-catalogs/{cat['id']}", headers=h).status_code == 204
    with pytest.raises(FileNotFoundError):
        storage.get(key)
    with SessionLocal() as s:
        assert s.query(CustomerCatalog).count() == 0


# --- isolation + permissions ---------------------------------------------------------------


def test_another_tenant_cannot_see_or_touch_them(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    cc = _make(client, h, cid).json()
    other = auth(register(client, "other3@catalog.example.com"))
    enable_catalog_flag(client, other["Authorization"].removeprefix("Bearer "))
    base = f"/api/supplier-catalogs/{cid}/customer-catalogs"
    for method, path, body in [
        ("POST", base, {"all_included": True}),
        ("POST", f"{base}/preview", {"all_included": True}),
        ("GET", base, None),
        ("GET", f"{base}/{cc['id']}/file", None),
        ("DELETE", f"{base}/{cc['id']}", None),
    ]:
        r = client.request(method, path, headers=other, json=body)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


def test_a_version_cannot_be_read_through_another_catalog(uploaded) -> None:
    client, h, cat = uploaded
    cc = _make(client, h, cat["id"]).json()
    from tests.catalog.pdfs import TestCell, make_pdf

    pdf = make_pdf([[TestCell(code="Q1")]])
    other = client.post(
        "/api/supplier-catalogs", headers=h, files={"file": ("b.pdf", pdf, "application/pdf")}
    ).json()
    r = client.get(f"/api/supplier-catalogs/{other['id']}/customer-catalogs/{cc['id']}/file",
                   headers=h)
    assert r.status_code == 404


def test_flag_off_hides_the_routes(client: TestClient) -> None:
    h = auth(register(client, "off4@catalog.example.com"))
    for method, path in [
        ("POST", "/api/supplier-catalogs/x/customer-catalogs"),
        ("POST", "/api/supplier-catalogs/x/customer-catalogs/preview"),
        ("GET", "/api/supplier-catalogs/x/customer-catalogs"),
        ("GET", "/api/supplier-catalogs/x/customer-catalogs/y/file"),
        ("DELETE", "/api/supplier-catalogs/x/customer-catalogs/y"),
    ]:
        assert client.request(method, path, headers=h, json={}).status_code == 404


def test_viewers_can_read_but_not_create_or_delete(uploaded) -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import User

    client, h, cat = uploaded
    cid = cat["id"]
    cc = _make(client, h, cid).json()
    me = client.get("/api/auth/me", headers=h).json()
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.id == me["id"]))
        assert u is not None
        u.role = "viewer"
        s.commit()
    tok = client.post("/api/auth/login", json={"email": me["email"], "password": "s3cret-pass"})
    vh = auth(tok.json()["access_token"])
    assert len(_list(client, vh, cid)) == 1
    assert len(_file(client, vh, cid, cc["id"])) > 1000
    assert client.post(f"/api/supplier-catalogs/{cid}/customer-catalogs/preview", headers=vh,
                       json={"all_included": True}).status_code == 200
    assert _make(client, vh, cid).status_code == 403
    assert client.delete(f"/api/supplier-catalogs/{cid}/customer-catalogs/{cc['id']}",
                         headers=vh).status_code == 403

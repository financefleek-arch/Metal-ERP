"""S3 API: label PDFs (direct and as a background job), preview, isolation, cleanup."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pymupdf
import pytest
import zxingcpp
from fastapi.testclient import TestClient
from PIL import Image

from app.services.catalog import output_jobs
from app.services.catalog.barcode import modules
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


def _items(client: TestClient, h: dict[str, str], cid: str, **params: Any) -> list[dict[str, Any]]:
    params.setdefault("limit", 200)
    r = client.get(f"/api/supplier-catalogs/{cid}/items", headers=h, params=params)
    return r.json()  # type: ignore[no-any-return]


def _labels(client: TestClient, h: dict[str, str], cid: str, body: dict[str, Any]) -> Any:
    return client.post(f"/api/supplier-catalogs/{cid}/labels", headers=h, json=body)


def _decode(pdf: bytes) -> list[str]:
    out: list[str] = []
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        out += [r.text for r in zxingcpp.read_barcodes(img)]
    return out


def _text(pdf: bytes) -> str:
    return " ".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


# --- barcode on the item list ----------------------------------------------------


def test_items_carry_the_barcode_pattern(uploaded) -> None:
    client, h, cat = uploaded
    first = _items(client, h, cat["id"])[0]
    assert first["barcode"] == modules(first["code"])
    assert set(first["barcode"]) == {"0", "1"}


# --- direct PDF ------------------------------------------------------------------


def test_all_included_gives_one_label_per_item_and_decodes(uploaded) -> None:
    client, h, cat = uploaded
    r = _labels(client, h, cat["id"], {"all_included": True})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="labels-glassware.pdf"'
    assert r.headers["X-Label-Count"] == "24" and r.headers["X-Page-Count"] == "24"
    assert r.headers["X-Scan-Warning"] == "0"
    codes = [i["code"] for i in _items(client, h, cat["id"])]
    assert sorted(_decode(r.content)) == sorted(codes)


def test_all_included_skips_excluded_but_explicit_ids_do_not(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    client.patch(
        f"/api/supplier-catalogs/{cat['id']}/items/{items[0]['id']}",
        headers=h,
        json={"included": False},
    )
    r = _labels(client, h, cat["id"], {"all_included": True})
    assert r.headers["X-Label-Count"] == "23"
    assert items[0]["code"] not in _decode(r.content)

    explicit = _labels(client, h, cat["id"], {"ids": [items[0]["id"]]})
    assert _decode(explicit.content) == [items[0]["code"]]


def test_ids_filter_and_copies(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    ids = [items[2]["id"], items[0]["id"]]  # given out of order
    r = _labels(client, h, cat["id"], {"ids": ids, "copies": 3})
    assert r.headers["X-Label-Count"] == "6"
    got = _decode(r.content)
    # printed in catalog order, each repeated
    assert got == [items[0]["code"]] * 3 + [items[2]["code"]] * 3

    n = len(_items(client, h, cat["id"], q="beer"))
    r = _labels(client, h, cat["id"], {"filter": {"q": "beer"}})
    assert r.headers["X-Label-Count"] == str(n)


def test_price_name_and_code_options_reach_the_pdf(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    client.patch(f"/api/supplier-catalogs/{cid}", headers=h, json={"multiplier": "1.25"})
    first = _items(client, h, cid)[0]
    body = {"ids": [first["id"]], "show_price": True}
    text = _text(_labels(client, h, cid, body).content)
    assert first["code"] in text and "Rs 374" in text  # 299 x 1.25 = 373.75 -> 374
    assert first["display_name"][:12] in text
    plain = _text(_labels(client, h, cid, {"ids": [first["id"]], "show_name": False,
                                           "show_code": False}).content)
    assert plain.strip() == ""


def test_sheet_preset_pages_and_start_slot(uploaded) -> None:
    client, h, cat = uploaded
    r = _labels(client, h, cat["id"], {"all_included": True, "preset": "sheet_a4_3x8"})
    assert r.headers["X-Page-Count"] == "1" and r.headers["X-Label-Count"] == "24"
    r = _labels(client, h, cat["id"],
                {"all_included": True, "preset": "sheet_a4_3x8", "start_at": 2})
    assert r.headers["X-Page-Count"] == "2"
    doc = pymupdf.open(stream=r.content, filetype="pdf")
    assert len(doc) == 2 and round(doc[0].rect.width / 72 * 25.4) == 210


@pytest.mark.parametrize(
    "body",
    [
        {},  # no selector
        {"ids": ["x"], "all_included": True},  # two selectors
        {"ids": [], "filter": {}},
        {"all_included": True, "copies": 0},
        {"all_included": True, "copies": 100},
        {"all_included": True, "preset": "roll_99x99"},
        {"all_included": True, "start_at": 25},
    ],
)
def test_bad_requests_are_422(uploaded, body: dict[str, Any]) -> None:
    client, h, cat = uploaded
    assert _labels(client, h, cat["id"], body).status_code == 422


def test_nothing_to_print_is_a_clear_422(uploaded) -> None:
    client, h, cat = uploaded
    r = _labels(client, h, cat["id"], {"filter": {"q": "zzzzzz"}})
    assert r.status_code == 422 and "No items" in r.json()["detail"]
    r = _labels(client, h, cat["id"], {"ids": ["not-an-item"]})
    assert r.status_code == 422


def test_too_many_labels_are_refused(uploaded, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, cat = uploaded
    monkeypatch.setattr(output_jobs, "MAX_LABELS", 10)
    r = _labels(client, h, cat["id"], {"all_included": True})
    assert r.status_code == 422 and "limit is 10" in r.json()["detail"]


# --- preview ---------------------------------------------------------------------


def test_preview_is_a_png_of_the_first_item(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    r = client.post(f"/api/supplier-catalogs/{cat['id']}/labels/preview", headers=h,
                    json={"ids": [items[1]["id"]], "show_price": True})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    w, hgt = Image.open(io.BytesIO(r.content)).size
    assert 1.9 < w / hgt < 2.1  # 50 x 25 mm
    empty = client.post(f"/api/supplier-catalogs/{cat['id']}/labels/preview", headers=h,
                        json={"filter": {"q": "zzzzzz"}})
    assert empty.status_code == 422


# --- background job --------------------------------------------------------------


@pytest.fixture
def small_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(output_jobs, "SYNC_LABEL_LIMIT", 5)


def test_big_runs_become_a_job_that_finishes_and_downloads(uploaded, small_sync) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    r = _labels(client, h, cid, {"all_included": True, "copies": 2})
    assert r.status_code == 202
    job = r.json()
    assert job["total"] == 48 and job["page_count"] == 48
    assert job["status"] in ("queued", "running", "done")

    polled = client.get(f"/api/supplier-catalogs/{cid}/outputs/{job['id']}", headers=h).json()
    assert polled["status"] == "done"
    assert polled["progress"] == polled["total"] == 48
    assert polled["error"] is None and polled["scan_warning"] is False

    f = client.get(f"/api/supplier-catalogs/{cid}/outputs/{job['id']}/file", headers=h)
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf"
    codes = sorted(i["code"] for i in _items(client, h, cid))
    assert sorted(_decode(f.content)) == sorted(codes * 2)


def test_job_failure_is_reported_on_the_job_not_as_a_500(
    uploaded, small_sync, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, h, cat = uploaded

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(output_jobs, "render_pdf", boom)
    r = _labels(client, h, cat["id"], {"all_included": True})
    assert r.status_code == 202
    base = f"/api/supplier-catalogs/{cat['id']}/outputs/{r.json()['id']}"
    job = client.get(base, headers=h).json()
    assert job["status"] == "error" and "renderer exploded" in job["error"]
    assert client.get(f"{base}/file", headers=h).status_code == 409


def _insert_job(catalog_id: str, tenant_id: str, status: str, age_minutes: int = 0) -> str:
    from app.db import SessionLocal
    from app.models import CatalogOutputJob

    with SessionLocal() as s:
        job = CatalogOutputJob(
            tenant_id=tenant_id, catalog_id=catalog_id, kind="labels", status=status,
            params_json={}, total=10,
        )
        s.add(job)
        s.flush()
        if age_minutes:
            job.updated_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
        s.commit()
        return job.id


def test_unfinished_job_file_is_409_and_stale_jobs_are_marked_failed(uploaded) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    tid = client.get("/api/auth/me", headers=h).json()["tenant_id"]
    running = _insert_job(cid, tid, "running")
    base = f"/api/supplier-catalogs/{cid}/outputs/{running}"
    assert client.get(f"{base}/file", headers=h).status_code == 409
    assert client.get(base, headers=h).json()["status"] == "running"

    stale = _insert_job(cid, tid, "running", age_minutes=30)
    got = client.get(f"/api/supplier-catalogs/{cid}/outputs/{stale}", headers=h).json()
    assert got["status"] == "error" and "interrupted" in got["error"]


def test_old_finished_jobs_are_pruned_with_their_files(
    uploaded, small_sync, storage: LocalStorage
) -> None:
    from app.db import SessionLocal
    from app.models import CatalogOutputJob

    client, h, cat = uploaded
    cid = cat["id"]
    first = _labels(client, h, cid, {"all_included": True}).json()
    key = None
    with SessionLocal() as s:
        j = s.get(CatalogOutputJob, first["id"])
        assert j is not None and j.result_key
        key = j.result_key
        j.updated_at = datetime.now(UTC) - timedelta(days=8)
        s.commit()
    assert storage.get(key)
    _labels(client, h, cid, {"all_included": True})  # creating a new job prunes the old one
    gone = client.get(f"/api/supplier-catalogs/{cid}/outputs/{first['id']}", headers=h)
    assert gone.status_code == 404
    with pytest.raises(FileNotFoundError):
        storage.get(key)


def test_deleting_a_catalog_removes_its_jobs_and_output_files(
    uploaded, small_sync, storage: LocalStorage
) -> None:
    from app.db import SessionLocal
    from app.models import CatalogOutputJob

    client, h, cat = uploaded
    job = _labels(client, h, cat["id"], {"all_included": True}).json()
    with SessionLocal() as s:
        row = s.get(CatalogOutputJob, job["id"])
        assert row is not None and row.result_key
        key = row.result_key
    assert storage.get(key)
    assert client.delete(f"/api/supplier-catalogs/{cat['id']}", headers=h).status_code == 204
    with pytest.raises(FileNotFoundError):
        storage.get(key)
    with SessionLocal() as s:
        assert s.query(CatalogOutputJob).count() == 0


# --- isolation + permissions -----------------------------------------------------


def test_another_tenant_cannot_use_labels_or_jobs(uploaded, small_sync) -> None:
    client, h, cat = uploaded
    cid = cat["id"]
    job = _labels(client, h, cid, {"all_included": True}).json()
    other = auth(register(client, "other2@catalog.example.com"))
    enable_catalog_flag(client, other["Authorization"].removeprefix("Bearer "))
    for method, path, body in [
        ("POST", f"/api/supplier-catalogs/{cid}/labels", {"all_included": True}),
        ("POST", f"/api/supplier-catalogs/{cid}/labels/preview", {"all_included": True}),
        ("GET", f"/api/supplier-catalogs/{cid}/outputs/{job['id']}", None),
        ("GET", f"/api/supplier-catalogs/{cid}/outputs/{job['id']}/file", None),
    ]:
        r = client.request(method, path, headers=other, json=body)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


def test_flag_off_hides_the_label_routes(client: TestClient) -> None:
    h = auth(register(client, "off3@catalog.example.com"))
    for method, path in [
        ("POST", "/api/supplier-catalogs/x/labels"),
        ("POST", "/api/supplier-catalogs/x/labels/preview"),
        ("GET", "/api/supplier-catalogs/x/outputs/y"),
        ("GET", "/api/supplier-catalogs/x/outputs/y/file"),
    ]:
        assert client.request(method, path, headers=h, json={}).status_code == 404


def test_viewers_can_preview_but_not_print(uploaded) -> None:
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
    tok = client.post("/api/auth/login", json={"email": me["email"], "password": "s3cret-pass"})
    vh = auth(tok.json()["access_token"])
    cid = cat["id"]
    assert client.post(f"/api/supplier-catalogs/{cid}/labels/preview", headers=vh,
                       json={"all_included": True}).status_code == 200
    assert _labels(client, vh, cid, {"all_included": True}).status_code == 403

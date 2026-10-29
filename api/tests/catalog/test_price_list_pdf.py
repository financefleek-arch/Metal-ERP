"""PDF in, PDF out: a customer PDF straight from a supplier price list, no items needed."""

from __future__ import annotations

from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Item
from app.services.catalog import supplier_pdf
from tests.catalog.conftest import seed_hsn
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
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], dict]:
    client, h, _ = catalog_client
    seed_hsn("7013", "7010", "7615", "7323")
    sup = _party(client, h, "Sugal Glass House")
    cat = _upload(client, h, data=_pdf(), name="a.pdf", supplier_party_id=sup).json()
    client.patch(f"{BASE}/{cat['id']}", headers=h, json={"bulk_margin_pct": "20"})
    return client, h, cat


def _make(client: TestClient, h: dict[str, str], cat: dict, **body: Any) -> Any:
    body = {"all_included": True, **body}
    return client.post(f"{BASE}/{cat['id']}/customer-pdf", headers=h, json=body)


def _text(client: TestClient, h: dict[str, str], cat: dict, job: dict) -> str:
    r = client.get(f"{BASE}/{cat['id']}/outputs/{job['id']}/file", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "catalog-" in r.headers["content-disposition"]
    return " ".join(p.get_text() for p in pymupdf.open(stream=r.content, filetype="pdf"))


def test_a_price_list_becomes_a_customer_pdf_without_adding_anything_to_items(shop) -> None:
    client, h, cat = shop
    r = _make(client, h, cat)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "done" and job["page_count"] >= 2 and job["total"] == 3
    text = _text(client, h, cat, job)
    rows = _items(client, h, cat["id"], limit=200)
    for row in rows:
        assert row["display_name"].lower() in text.lower()
        assert row["code"] in text
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Item)) == 0


def test_the_pdf_never_carries_cost_prices_or_supplier_codes(shop) -> None:
    client, h, cat = shop
    job = _make(client, h, cat).json()
    text = _text(client, h, cat, job)
    for row in _items(client, h, cat["id"], limit=200):
        assert f"{float(row['sell_price']):.2f}".rstrip("0").rstrip(".") in text.replace(",", "")
        if float(row["cost_price"]) != float(row["sell_price"]):
            assert f"{float(row['cost_price']):.2f}" not in text
        assert row["supplier_code"] not in text


def test_the_same_request_is_not_rendered_again_until_something_changes(shop) -> None:
    client, h, cat = shop
    first = _make(client, h, cat).json()
    again = _make(client, h, cat).json()
    assert again["id"] == first["id"]  # the stored PDF, not a new build
    # a layout change is a different PDF
    other = _make(client, h, cat, columns=2).json()
    assert other["id"] != first["id"]
    # so is any edit: the margin moves every price
    client.patch(f"{BASE}/{cat['id']}", headers=h, json={"bulk_margin_pct": "35"})
    edited = _make(client, h, cat).json()
    assert edited["id"] not in (first["id"], other["id"])
    assert _make(client, h, cat).json()["id"] == edited["id"]


def test_only_the_chosen_products_are_printed(shop) -> None:
    client, h, cat = shop
    rows = _items(client, h, cat["id"], limit=200)
    one = rows[0]
    job = _make(client, h, cat, all_included=False, ids=[one["id"]], title="Just one").json()
    assert job["total"] == 1
    text = _text(client, h, cat, job)
    assert one["display_name"].lower() in text.lower()
    others = [r for r in rows if r["id"] != one["id"]]
    assert all(r["display_name"].lower() not in text.lower() for r in others)
    # an excluded row is left out of "all included"
    client.patch(f"{BASE}/{cat['id']}/items/{one['id']}", headers=h, json={"included": False})
    all_ = _make(client, h, cat).json()
    assert all_["total"] == 2


def test_check_counts_and_empty_selections_are_refused(shop) -> None:
    client, h, cat = shop
    c = client.post(f"{BASE}/{cat['id']}/customer-pdf/check", headers=h, json={"all_included": True})
    assert c.status_code == 200, c.text
    assert c.json()["selected"] == 3 and c.json()["included"] == 3 and c.json()["no_price"] == 0
    both = _make(client, h, cat, ids=["x"])
    assert both.status_code == 422
    nothing = _make(client, h, cat, all_included=False, ids=["no-such-row"])
    assert nothing.status_code == 422


def test_a_big_list_is_built_in_the_background(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, cat = shop
    monkeypatch.setattr(supplier_pdf, "SYNC_LIMIT", 1)
    job = _make(client, h, cat).json()
    polled = client.get(f"{BASE}/{cat['id']}/outputs/{job['id']}", headers=h).json()
    assert polled["status"] == "done"  # the test client runs background tasks before returning


def test_another_firms_price_list_is_not_reachable(shop) -> None:
    client, h, cat = shop
    from tests.catalog.conftest import auth, register

    other = auth(register(client, email="other@example.com"))
    r = client.post(f"{BASE}/{cat['id']}/customer-pdf", headers=other, json={"all_included": True})
    assert r.status_code == 404

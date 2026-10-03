"""Pricing by margin %: the bulk margin, item-level margins, rounding step, bulk edits."""

from __future__ import annotations

import random
import time
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import SupplierCatalog, SupplierCatalogItem
from app.services.catalog.pricing import sell_price
from app.services.catalog.reprice import reprice
from tests.catalog.conftest import SAMPLE_PDF

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
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def _patch_cat(client: TestClient, h: dict[str, str], cid: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/api/supplier-catalogs/{cid}", headers=h, json=body)


def _patch_item(
    client: TestClient, h: dict[str, str], cid: str, iid: str, body: dict[str, Any]
) -> Any:
    return client.patch(f"/api/supplier-catalogs/{cid}/items/{iid}", headers=h, json=body)


def _bulk(client: TestClient, h: dict[str, str], cid: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/api/supplier-catalogs/{cid}/items/bulk", headers=h, json=body)


def _expected(item: dict[str, Any], margin: str, step: int) -> str:
    return str(sell_price(Decimal(item["cost_price"]), Decimal(margin), step))


# --- bulk margin ---------------------------------------------------------------


def test_bulk_margin_reprices_every_item(uploaded) -> None:
    client, h, cat = uploaded
    assert cat["bulk_margin_pct"] == "0.00"
    r = _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    assert r.status_code == 200, r.text
    assert r.json()["bulk_margin_pct"] == "25.00"
    items = _items(client, h, cat["id"])
    assert len(items) == 24
    for it in items:
        assert it["sell_price"] == _expected(it, "25", 1), it["code"]
    assert items[0]["cost_price"] == "299.00"  # the supplier price is never touched
    assert items[0]["sell_price"] == "374.00"  # 299 + 25% = 373.75


def test_a_negative_margin_is_a_discount(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "-10"})
    first = _items(client, h, cat["id"])[0]
    assert first["sell_price"] == "269.00"  # 299 - 10% = 269.1


def test_margin_accepts_two_decimals(uploaded) -> None:
    client, h, cat = uploaded
    r = _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "12.55"})
    assert r.status_code == 200 and r.json()["bulk_margin_pct"] == "12.55"
    first = _items(client, h, cat["id"])[0]
    assert first["sell_price"] == _expected(first, "12.55", 1)  # 336.53 -> 337


def test_rounding_step_changes_prices(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25", "rounding_step": 5})
    for it in _items(client, h, cat["id"]):
        assert it["sell_price"] == _expected(it, "25", 5)
        assert Decimal(it["sell_price"]) % 5 == 0
    _patch_cat(client, h, cat["id"], {"rounding_step": 10})
    for it in _items(client, h, cat["id"]):
        assert Decimal(it["sell_price"]) % 10 == 0
    got = client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h).json()
    assert got["rounding_step"] == 10


@pytest.mark.parametrize(
    "body",
    [
        {"bulk_margin_pct": "-100"},
        {"bulk_margin_pct": "-150"},
        {"bulk_margin_pct": "1000.01"},
        {"bulk_margin_pct": "12.345"},
        {"bulk_margin_pct": "abc"},
        {"rounding_step": 7},
        {"rounding_step": 0},
    ],
)
def test_bad_pricing_values_are_422_and_change_nothing(uploaded, body: dict[str, Any]) -> None:
    client, h, cat = uploaded
    assert _patch_cat(client, h, cat["id"], body).status_code == 422
    assert _items(client, h, cat["id"])[0]["sell_price"] == "299.00"


def test_edge_margins_accepted(uploaded) -> None:
    client, h, cat = uploaded
    for ok in ("1000", "-99.99", "0"):
        assert _patch_cat(client, h, cat["id"], {"bulk_margin_pct": ok}).status_code == 200, ok


def test_zero_margin_means_the_supplier_price(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "0"})
    for it in _items(client, h, cat["id"]):
        assert it["sell_price"] == it["cost_price"]


def test_same_margin_again_is_a_no_op(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    before = _items(client, h, cat["id"])
    assert _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"}).status_code == 200
    assert _items(client, h, cat["id"]) == before


def test_rename_alone_does_not_reprice(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": "100"})
    before = _items(client, h, cat["id"])
    _patch_cat(client, h, cat["id"], {"title": "New name"})
    assert _items(client, h, cat["id"]) == before


def test_excluded_items_are_repriced_too(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"included": False})
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "50"})
    again = _items(client, h, cat["id"])[0]
    assert again["included"] is False
    assert again["sell_price"] == _expected(again, "50", 1)


# --- item-level margin ---------------------------------------------------------


def test_item_margin_wins_and_survives_bulk_changes(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    a, b = items[0], items[1]
    out = _patch_item(client, h, cat["id"], a["id"], {"item_margin_pct": "40"}).json()
    assert out["item_margin_pct"] == "40.00"
    assert out["sell_price"] == _expected(a, "40", 1)

    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    after = {i["id"]: i for i in _items(client, h, cat["id"])}
    assert after[a["id"]]["sell_price"] == _expected(a, "40", 1)  # unchanged by the bulk margin
    assert after[b["id"]]["sell_price"] == _expected(b, "25", 1)

    _patch_cat(client, h, cat["id"], {"rounding_step": 5})  # rounding applies to both
    again = {i["id"]: i for i in _items(client, h, cat["id"])}
    assert again[a["id"]]["sell_price"] == _expected(a, "40", 5)


def test_an_item_margin_of_zero_is_a_real_margin(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    it = _items(client, h, cat["id"])[0]
    out = _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": "0"}).json()
    assert out["item_margin_pct"] == "0.00"
    assert out["sell_price"] == out["cost_price"]  # sold at the supplier price, not +25%
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "30"})
    again = _items(client, h, cat["id"])[0]
    assert again["sell_price"] == again["cost_price"]


def test_clearing_an_item_margin_returns_to_the_bulk_margin(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": "100"})
    out = _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": None}).json()
    assert out["item_margin_pct"] is None
    assert out["sell_price"] == _expected(it, "25", 1)


def test_cost_edit_uses_the_item_margin(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": "50"})
    out = _patch_item(client, h, cat["id"], it["id"], {"cost_price": "200"}).json()
    assert out["sell_price"] == "300.00"


def test_item_edit_without_the_margin_key_leaves_it_alone(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": "40"})
    out = _patch_item(client, h, cat["id"], it["id"], {"display_name": "Renamed"}).json()
    assert out["item_margin_pct"] == "40.00"


@pytest.mark.parametrize("bad", ["-100", "-250", "1000.01", "12.345", "x"])
def test_bad_item_margin_is_422(uploaded, bad: str) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    assert _patch_item(client, h, cat["id"], it["id"], {"item_margin_pct": bad}).status_code == 422


# --- bulk edits of item margins --------------------------------------------------


def test_bulk_item_margin_by_ids(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    items = _items(client, h, cat["id"])
    ids = [i["id"] for i in items[:3]]
    r = _bulk(client, h, cat["id"], {"ids": ids, "changes": {"item_margin_pct": "60"}})
    assert r.json() == {"updated": 3}
    after = {i["id"]: i for i in _items(client, h, cat["id"])}
    for i in items[:3]:
        assert after[i["id"]]["item_margin_pct"] == "60.00"
        assert after[i["id"]]["sell_price"] == _expected(i, "60", 1)
    for i in items[3:6]:
        assert after[i["id"]]["item_margin_pct"] is None
        assert after[i["id"]]["sell_price"] == _expected(i, "25", 1)


def test_bulk_item_margin_by_filter_then_clear(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "25"})
    n = len(client.get(f"/api/supplier-catalogs/{cat['id']}/items", headers=h,
                       params={"q": "beer", "limit": 200}).json())
    body = {"filter": {"q": "beer"}, "changes": {"item_margin_pct": "80"}}
    assert _bulk(client, h, cat["id"], body).json() == {"updated": n}
    beer = [i for i in _items(client, h, cat["id"]) if i["item_margin_pct"] == "80.00"]
    assert len(beer) == n
    assert all(i["sell_price"] == _expected(i, "80", 1) for i in beer)

    body = {"filter": {"q": "beer"}, "changes": {"item_margin_pct": None}}
    assert _bulk(client, h, cat["id"], body).json() == {"updated": n}
    for i in _items(client, h, cat["id"]):
        assert i["item_margin_pct"] is None
        assert i["sell_price"] == _expected(i, "25", 1)


def test_bulk_item_margin_and_group_together(uploaded) -> None:
    client, h, cat = uploaded
    ids = [i["id"] for i in _items(client, h, cat["id"])[:2]]
    r = _bulk(client, h, cat["id"], {"ids": ids,
              "changes": {"item_margin_pct": "100", "group_name": "Premium"}})
    assert r.json() == {"updated": 2}
    first = _items(client, h, cat["id"])[0]
    assert (first["category_name"], first["item_margin_pct"]) == ("Premium", "100.00")


def test_bulk_ignores_ids_from_another_catalog_when_repricing(catalog_client: Env) -> None:
    client, h, _ = catalog_client
    a = client.post("/api/supplier-catalogs", headers=h,
                    files={"file": ("a.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")}).json()
    from tests.catalog.pdfs import TestCell, make_pdf

    other = make_pdf([[TestCell(code="Q1")]])
    b = client.post(
        "/api/supplier-catalogs", headers=h, files={"file": ("b.pdf", other, "application/pdf")}
    ).json()
    a_ids = [i["id"] for i in _items(client, h, a["id"])[:2]]
    r = _bulk(client, h, b["id"], {"ids": a_ids, "changes": {"item_margin_pct": "200"}})
    assert r.json() == {"updated": 0}
    assert all(i["item_margin_pct"] is None for i in _items(client, h, a["id"]))


# --- item-level margin count + filter --------------------------------------------


def test_detail_reports_how_many_items_have_their_own_margin(uploaded) -> None:
    client, h, cat = uploaded
    url = f"/api/supplier-catalogs/{cat['id']}"
    assert client.get(url, headers=h).json()["item_margin_count"] == 0
    items = _items(client, h, cat["id"])
    _patch_item(client, h, cat["id"], items[0]["id"], {"item_margin_pct": "40"})
    _bulk(client, h, cat["id"],
          {"ids": [items[1]["id"], items[2]["id"]], "changes": {"item_margin_pct": "100"}})
    assert client.get(url, headers=h).json()["item_margin_count"] == 3
    repriced = _patch_cat(client, h, cat["id"], {"bulk_margin_pct": "10"})
    assert repriced.json()["item_margin_count"] == 3
    _bulk(client, h, cat["id"],
          {"filter": {"has_item_margin": True}, "changes": {"item_margin_pct": None}})
    assert client.get(url, headers=h).json()["item_margin_count"] == 0


def test_has_item_margin_filter(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    _bulk(client, h, cat["id"],
          {"ids": [items[0]["id"], items[1]["id"]], "changes": {"item_margin_pct": "70"}})
    base = f"/api/supplier-catalogs/{cat['id']}/items"
    own = client.get(base, headers=h, params={"has_item_margin": "true", "limit": 200}).json()
    rest = client.get(base, headers=h, params={"has_item_margin": "false", "limit": 200}).json()
    assert {i["id"] for i in own} == {items[0]["id"], items[1]["id"]}
    assert len(rest) == 22
    counted = client.get(base, headers=h, params={"has_item_margin": "true"})
    assert counted.headers["X-Total-Count"] == "2"


# --- numeric exactness + scale ---------------------------------------------------


def _make_catalog(session: Session, tenant_id: str, n: int, seed: int = 7) -> SupplierCatalog:
    rnd = random.Random(seed)
    cat = SupplierCatalog(
        tenant_id=tenant_id, title="Synthetic", source_filename="s.pdf",
        source_sha256="f" * 64, code_prefix="SY", bulk_margin_pct=Decimal("0"), rounding_step=1,
    )
    session.add(cat)
    session.flush()
    for i in range(n):
        cost = Decimal(rnd.randint(1, 200000)) / 100
        own = Decimal(rnd.randint(-5000, 20000)) / 100 if i % 5 == 0 else None
        session.add(SupplierCatalogItem(
            tenant_id=tenant_id, catalog_id=cat.id, page_no=i // 12 + 1, position=i % 12 + 1,
            supplier_code=f"S{i}", code=f"SY-{i + 1:06d}", name_raw="n", display_name="n",
            cost_price=cost, item_margin_pct=own, sell_price=cost,
        ))
    session.flush()
    return cat


@pytest.mark.parametrize("step", [1, 5, 10])
def test_reprice_matches_the_reference_function_for_random_data(
    session: Session, tenant_id: str, step: int
) -> None:
    cat = _make_catalog(session, tenant_id, 300)
    cat.bulk_margin_pct = Decimal(random.Random(step).randint(-5000, 40000)) / 100
    cat.rounding_step = step
    reprice(session, cat)
    session.flush()
    session.expire_all()
    for it in session.query(SupplierCatalogItem).filter_by(catalog_id=cat.id):
        margin = it.item_margin_pct if it.item_margin_pct is not None else cat.bulk_margin_pct
        assert it.sell_price == sell_price(it.cost_price, Decimal(margin), step)


def test_reprice_reports_only_real_changes(session: Session, tenant_id: str) -> None:
    cat = _make_catalog(session, tenant_id, 50)
    cat.bulk_margin_pct = Decimal("37")
    first = reprice(session, cat)
    session.flush()
    assert first > 0
    assert reprice(session, cat) == 0  # nothing left to change


def test_a_two_thousand_item_catalog_reprices_quickly(
    catalog_client: Env, session: Session
) -> None:
    client, h, tid = catalog_client
    cat = _make_catalog(session, tid, 2000)
    session.commit()
    t = time.time()
    r = _patch_cat(client, h, cat.id, {"bulk_margin_pct": "20", "rounding_step": 5})
    elapsed = time.time() - t
    assert r.status_code == 200
    assert elapsed < 5, f"repricing 2,000 items took {elapsed:.1f}s"
    sample = client.get(f"/api/supplier-catalogs/{cat.id}/items", headers=h,
                        params={"limit": 50}).json()
    for it in sample:
        margin = it["item_margin_pct"] or "20"
        assert it["sell_price"] == _expected(it, margin, 5)

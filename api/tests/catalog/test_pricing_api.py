"""S2: catalog multiplier, per-item override, rounding step, bulk overrides."""

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


def _expected(item: dict[str, Any], mult: str, step: int) -> str:
    return str(sell_price(Decimal(item["cost_price"]), Decimal(mult), step))


# --- catalog multiplier --------------------------------------------------------


def test_multiplier_reprices_every_item(uploaded) -> None:
    client, h, cat = uploaded
    r = _patch_cat(client, h, cat["id"], {"multiplier": "1.25"})
    assert r.status_code == 200, r.text
    assert r.json()["multiplier"] == "1.250"
    items = _items(client, h, cat["id"])
    assert len(items) == 24
    for it in items:
        assert it["sell_price"] == _expected(it, "1.25", 1), it["code"]
    # supplier price is never touched
    assert items[0]["cost_price"] == "299.00"
    assert items[0]["sell_price"] == "374.00"  # 299 x 1.25 = 373.75


def test_multiplier_below_one_is_allowed(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"multiplier": "0.9"})
    first = _items(client, h, cat["id"])[0]
    assert first["sell_price"] == "269.00"  # 299 x 0.9 = 269.1


def test_rounding_step_changes_prices(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"multiplier": "1.25", "rounding_step": 5})
    for it in _items(client, h, cat["id"]):
        assert it["sell_price"] == _expected(it, "1.25", 5)
        assert Decimal(it["sell_price"]) % 5 == 0
    _patch_cat(client, h, cat["id"], {"rounding_step": 10})
    for it in _items(client, h, cat["id"]):
        assert Decimal(it["sell_price"]) % 10 == 0
    got = client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h).json()
    assert got["rounding_step"] == 10


@pytest.mark.parametrize(
    "body",
    [
        {"multiplier": "0"},
        {"multiplier": "-1.2"},
        {"multiplier": "100"},
        {"multiplier": "1.2345"},
        {"multiplier": "abc"},
        {"rounding_step": 7},
        {"rounding_step": 0},
    ],
)
def test_bad_pricing_values_are_422_and_change_nothing(uploaded, body: dict[str, Any]) -> None:
    client, h, cat = uploaded
    assert _patch_cat(client, h, cat["id"], body).status_code == 422
    assert _items(client, h, cat["id"])[0]["sell_price"] == "299.00"


def test_edge_multipliers_accepted(uploaded) -> None:
    client, h, cat = uploaded
    assert _patch_cat(client, h, cat["id"], {"multiplier": "99.999"}).status_code == 200
    assert _patch_cat(client, h, cat["id"], {"multiplier": "0.001"}).status_code == 200


def test_same_multiplier_again_is_a_no_op(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"multiplier": "1.25"})
    before = _items(client, h, cat["id"])
    assert _patch_cat(client, h, cat["id"], {"multiplier": "1.25"}).status_code == 200
    assert _items(client, h, cat["id"]) == before


def test_rename_alone_does_not_reprice(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"multiplier_override": "2"})
    before = _items(client, h, cat["id"])
    _patch_cat(client, h, cat["id"], {"title": "New name"})
    assert _items(client, h, cat["id"]) == before


def test_excluded_items_are_repriced_too(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"included": False})
    _patch_cat(client, h, cat["id"], {"multiplier": "1.5"})
    again = _items(client, h, cat["id"])[0]
    assert again["included"] is False
    assert again["sell_price"] == _expected(again, "1.5", 1)


# --- per-item override ---------------------------------------------------------


def test_override_wins_and_survives_catalog_changes(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    a, b = items[0], items[1]
    out = _patch_item(client, h, cat["id"], a["id"], {"multiplier_override": "1.4"}).json()
    assert out["multiplier_override"] == "1.400"
    assert out["sell_price"] == _expected(a, "1.4", 1)

    _patch_cat(client, h, cat["id"], {"multiplier": "1.25"})
    after = {i["id"]: i for i in _items(client, h, cat["id"])}
    assert after[a["id"]]["sell_price"] == _expected(a, "1.4", 1)  # unchanged by the catalog
    assert after[b["id"]]["sell_price"] == _expected(b, "1.25", 1)

    _patch_cat(client, h, cat["id"], {"rounding_step": 5})  # rounding applies to overrides too
    again = {i["id"]: i for i in _items(client, h, cat["id"])}
    assert again[a["id"]]["sell_price"] == _expected(a, "1.4", 5)


def test_clearing_an_override_returns_to_the_catalog_multiplier(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"multiplier": "1.25"})
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"multiplier_override": "2"})
    out = _patch_item(client, h, cat["id"], it["id"], {"multiplier_override": None}).json()
    assert out["multiplier_override"] is None
    assert out["sell_price"] == _expected(it, "1.25", 1)


def test_cost_edit_uses_the_override(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"multiplier_override": "1.5"})
    out = _patch_item(client, h, cat["id"], it["id"], {"cost_price": "200"}).json()
    assert out["sell_price"] == "300.00"


def test_item_edit_without_override_key_leaves_it_alone(uploaded) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    _patch_item(client, h, cat["id"], it["id"], {"multiplier_override": "1.4"})
    out = _patch_item(client, h, cat["id"], it["id"], {"display_name": "Renamed"}).json()
    assert out["multiplier_override"] == "1.400"


@pytest.mark.parametrize("bad", ["0", "-1", "100", "1.2345", "x"])
def test_bad_override_is_422(uploaded, bad: str) -> None:
    client, h, cat = uploaded
    it = _items(client, h, cat["id"])[0]
    assert (
        _patch_item(client, h, cat["id"], it["id"], {"multiplier_override": bad}).status_code == 422
    )


# --- bulk overrides ------------------------------------------------------------


def _bulk(client: TestClient, h: dict[str, str], cid: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/api/supplier-catalogs/{cid}/items/bulk", headers=h, json=body)


def test_bulk_override_by_ids(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"multiplier": "1.25"})
    items = _items(client, h, cat["id"])
    ids = [i["id"] for i in items[:3]]
    r = _bulk(client, h, cat["id"], {"ids": ids, "changes": {"multiplier_override": "1.6"}})
    assert r.json() == {"updated": 3}
    after = {i["id"]: i for i in _items(client, h, cat["id"])}
    for i in items[:3]:
        assert after[i["id"]]["multiplier_override"] == "1.600"
        assert after[i["id"]]["sell_price"] == _expected(i, "1.6", 1)
    for i in items[3:6]:
        assert after[i["id"]]["multiplier_override"] is None
        assert after[i["id"]]["sell_price"] == _expected(i, "1.25", 1)


def test_bulk_override_by_filter_then_clear(uploaded) -> None:
    client, h, cat = uploaded
    _patch_cat(client, h, cat["id"], {"multiplier": "1.25"})
    n = len(client.get(f"/api/supplier-catalogs/{cat['id']}/items", headers=h,
                       params={"q": "beer", "limit": 200}).json())
    body = {"filter": {"q": "beer"}, "changes": {"multiplier_override": "1.8"}}
    r = _bulk(client, h, cat["id"], body)
    assert r.json() == {"updated": n}
    beer = [i for i in _items(client, h, cat["id"]) if i["multiplier_override"] == "1.800"]
    assert len(beer) == n
    assert all(i["sell_price"] == _expected(i, "1.8", 1) for i in beer)

    body = {"filter": {"q": "beer"}, "changes": {"multiplier_override": None}}
    r = _bulk(client, h, cat["id"], body)
    assert r.json() == {"updated": n}
    for i in _items(client, h, cat["id"]):
        assert i["multiplier_override"] is None
        assert i["sell_price"] == _expected(i, "1.25", 1)


def test_bulk_override_and_group_together(uploaded) -> None:
    client, h, cat = uploaded
    ids = [i["id"] for i in _items(client, h, cat["id"])[:2]]
    r = _bulk(client, h, cat["id"], {"ids": ids,
              "changes": {"multiplier_override": "2", "group_name": "Premium"}})
    assert r.json() == {"updated": 2}
    first = _items(client, h, cat["id"])[0]
    assert (first["category_name"], first["multiplier_override"]) == ("Premium", "2.000")


def test_bulk_ignores_ids_from_another_catalog_when_repricing(catalog_client: Env) -> None:
    client, h, _ = catalog_client
    a = client.post("/api/supplier-catalogs", headers=h,
                    files={"file": ("a.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")}).json()
    from tests.catalog.pdfs import TestCell, make_pdf

    other = make_pdf([[TestCell(code="Q1")]])
    b = client.post(
        "/api/supplier-catalogs",
        headers=h,
        files={"file": ("b.pdf", other, "application/pdf")},
    ).json()
    a_ids = [i["id"] for i in _items(client, h, a["id"])[:2]]
    r = _bulk(client, h, b["id"], {"ids": a_ids, "changes": {"multiplier_override": "3"}})
    assert r.json() == {"updated": 0}
    assert all(i["multiplier_override"] is None for i in _items(client, h, a["id"]))


# --- numeric exactness + scale -------------------------------------------------


def _make_catalog(session: Session, tenant_id: str, n: int, seed: int = 7) -> SupplierCatalog:
    rnd = random.Random(seed)
    cat = SupplierCatalog(
        tenant_id=tenant_id, title="Synthetic", source_filename="s.pdf",
        source_sha256="f" * 64, code_prefix="SY", multiplier=Decimal("1.000"), rounding_step=1,
    )
    session.add(cat)
    session.flush()
    for i in range(n):
        cost = Decimal(rnd.randint(1, 200000)) / 100
        override = Decimal(rnd.randint(500, 3000)) / 1000 if i % 5 == 0 else None
        session.add(SupplierCatalogItem(
            tenant_id=tenant_id, catalog_id=cat.id, page_no=i // 12 + 1, position=i % 12 + 1,
            supplier_code=f"S{i}", code=f"SY-{i + 1:06d}", name_raw="n", display_name="n",
            cost_price=cost, multiplier_override=override, sell_price=cost,
        ))
    session.flush()
    return cat


@pytest.mark.parametrize("step", [1, 5, 10])
def test_reprice_matches_the_reference_function_for_random_data(
    session: Session, tenant_id: str, step: int
) -> None:
    cat = _make_catalog(session, tenant_id, 300)
    cat.multiplier = Decimal(random.Random(step).randint(1, 4000)) / 1000
    cat.rounding_step = step
    reprice(session, cat)
    session.flush()
    session.expire_all()
    for it in session.query(SupplierCatalogItem).filter_by(catalog_id=cat.id):
        mult = it.multiplier_override if it.multiplier_override is not None else cat.multiplier
        assert it.sell_price == sell_price(it.cost_price, Decimal(mult), step)


def test_reprice_reports_only_real_changes(session: Session, tenant_id: str) -> None:
    cat = _make_catalog(session, tenant_id, 50)
    cat.multiplier = Decimal("1.37")
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
    r = _patch_cat(client, h, cat.id, {"multiplier": "1.2", "rounding_step": 5})
    elapsed = time.time() - t
    assert r.status_code == 200
    assert elapsed < 5, f"repricing 2,000 items took {elapsed:.1f}s"
    sample = client.get(f"/api/supplier-catalogs/{cat.id}/items", headers=h,
                        params={"limit": 50}).json()
    for it in sample:
        mult = it["multiplier_override"] or "1.2"
        assert it["sell_price"] == _expected(it, mult, 5)


# --- override count + filter ---------------------------------------------------


def test_detail_reports_how_many_items_have_their_own_multiplier(uploaded) -> None:
    client, h, cat = uploaded
    detail = client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h)
    assert detail.json()["override_count"] == 0
    items = _items(client, h, cat["id"])
    _patch_item(client, h, cat["id"], items[0]["id"], {"multiplier_override": "1.4"})
    _bulk(client, h, cat["id"],
          {"ids": [items[1]["id"], items[2]["id"]], "changes": {"multiplier_override": "2"}})
    got = client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h).json()
    assert got["override_count"] == 3
    # the PATCH response carries it too
    r = _patch_cat(client, h, cat["id"], {"multiplier": "1.1"})
    assert r.json()["override_count"] == 3
    # clearing them all
    _bulk(client, h, cat["id"],
          {"filter": {"has_override": True}, "changes": {"multiplier_override": None}})
    final = client.get(f"/api/supplier-catalogs/{cat['id']}", headers=h)
    assert final.json()["override_count"] == 0


def test_has_override_filter(uploaded) -> None:
    client, h, cat = uploaded
    items = _items(client, h, cat["id"])
    _bulk(client, h, cat["id"],
          {"ids": [items[0]["id"], items[1]["id"]], "changes": {"multiplier_override": "1.7"}})
    base = f"/api/supplier-catalogs/{cat['id']}/items"
    own = client.get(base, headers=h, params={"has_override": "true", "limit": 200}).json()
    rest = client.get(base, headers=h, params={"has_override": "false", "limit": 200}).json()
    assert {i["id"] for i in own} == {items[0]["id"], items[1]["id"]}
    assert len(rest) == 22
    counted = client.get(base, headers=h, params={"has_override": "true"})
    assert counted.headers["X-Total-Count"] == "2"

"""Bulk item operations: PATCH /api/items/bulk, POST /api/items/bulk-delete.

dry-run parity, skip-if-equal, BULK-skips-discount, HSN fill, per-row error
isolation, id cap, cross-tenant id, bulk recategorize teaches classify rules,
delete guard + archive-on-blocked.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import HsnCode, Item, ItemClassifyRule, ProductGroup


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _seed_hsn(session) -> None:  # type: ignore[no-untyped-def]
    for code, desc, ch, rate in (
        ("73239390", "SS household/kitchen articles, other", "73", 12.0),
        ("72193590", "Flat-rolled stainless steel, cold-rolled, < 3mm", "72", 18.0),
    ):
        if session.scalar(select(HsnCode).where(HsnCode.code == code)) is None:
            session.add(HsnCode(code=code, description=desc, chapter=ch, default_gst_rate=rate))
    session.commit()


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _mk(client: TestClient, h: dict[str, str], name: str, **extra: object) -> dict:
    r = client.post("/api/items", headers=h, json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# bulk update
# --------------------------------------------------------------------------


def test_bulk_set_uom_dry_run_matches_real(client: TestClient) -> None:
    h = _h(_token(client, "bulk-1@x.example.com"))
    a = _mk(client, h, "Kadai 240MM")
    b = _mk(client, h, "Kadai 260MM")
    c = _mk(client, h, "Tawa 280MM", uom="pcs")  # already pcs -> skip
    ids = [a["id"], b["id"], c["id"]]
    payload = {"ids": ids, "fields": {"uom": "pcs"}, "fields_set": ["uom"]}

    dry = client.patch("/api/items/bulk?dry_run=true", headers=h, json=payload).json()
    assert dry["dry_run"] is True
    assert (dry["changed"], dry["unchanged"]) == (2, 1)
    assert {r["result"] for r in dry["rows"]} == {"changed", "skipped"}
    # nothing persisted
    assert client.get(f"/api/items/{a['id']}", headers=h).json()["uom"] is None

    real = client.patch("/api/items/bulk", headers=h, json=payload).json()
    assert (real["changed"], real["unchanged"]) == (2, 1)
    assert [r["result"] for r in real["rows"]] == [r["result"] for r in dry["rows"]]
    assert client.get(f"/api/items/{a['id']}", headers=h).json()["uom"] == "pcs"
    assert client.get(f"/api/items/{c['id']}", headers=h).json()["uom"] == "pcs"


def test_bulk_discount_skips_bulk_items(client: TestClient) -> None:
    h = _h(_token(client, "bulk-2@x.example.com"))
    mrp = _mk(client, h, "Hawkins Contura 3L", item_type="mrp")
    blk = _mk(client, h, "Kadai 240MM", item_type="bulk")
    res = client.patch(
        "/api/items/bulk",
        headers=h,
        json={
            "ids": [mrp["id"], blk["id"]],
            "fields": {"default_discount_pct": "5"},
            "fields_set": ["default_discount_pct"],
        },
    ).json()
    by_id = {r["id"]: r for r in res["rows"]}
    assert by_id[mrp["id"]]["result"] == "changed"
    assert by_id[blk["id"]]["result"] == "skipped"
    assert "not applicable" in by_id[blk["id"]]["detail"]
    assert client.get(f"/api/items/{mrp['id']}", headers=h).json()["default_discount_pct"] == "5.00"


def test_bulk_hsn_fills_gst_rate(client: TestClient) -> None:
    h = _h(_token(client, "bulk-3@x.example.com"))
    a = _mk(client, h, "SS Sheet A")
    b = _mk(client, h, "SS Sheet B")
    client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [a["id"], b["id"]], "fields": {"hsn_code": "72193590"},
              "fields_set": ["hsn_code"]},
    )
    assert float(client.get(f"/api/items/{a['id']}", headers=h).json()["gst_rate"]) == 18.0


def test_bulk_disabled_field_not_touched(client: TestClient) -> None:
    """fields carries a value but fields_set omits it -> untouched."""
    h = _h(_token(client, "bulk-4@x.example.com"))
    it = _mk(client, h, "Item X", default_rate="100.00")
    res = client.patch(
        "/api/items/bulk",
        headers=h,
        json={
            "ids": [it["id"]],
            "fields": {"uom": "kg", "default_rate": "999.00"},
            "fields_set": ["uom"],  # only uom enabled
        },
    ).json()
    assert res["changed"] == 1
    full = client.get(f"/api/items/{it['id']}", headers=h).json()
    assert full["uom"] == "kg"
    assert full["default_rate"] == "100.00"  # rate left alone


def test_bulk_id_cap_422(client: TestClient) -> None:
    h = _h(_token(client, "bulk-5@x.example.com"))
    r = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [f"x{n}" for n in range(501)], "fields": {"uom": "kg"},
              "fields_set": ["uom"]},
    )
    assert r.status_code == 422


def test_bulk_no_enabled_fields_422(client: TestClient) -> None:
    h = _h(_token(client, "bulk-6@x.example.com"))
    it = _mk(client, h, "Item Y")
    r = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [it["id"]], "fields": {"uom": "kg"}, "fields_set": []},
    )
    assert r.status_code == 422


def test_bulk_non_editable_field_422(client: TestClient) -> None:
    h = _h(_token(client, "bulk-6b@x.example.com"))
    it = _mk(client, h, "Item Z")
    r = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [it["id"]], "fields": {"name": "Renamed"}, "fields_set": ["name"]},
    )
    assert r.status_code == 422


def test_bulk_cross_tenant_id_is_error_row_others_proceed(client: TestClient) -> None:
    h1 = _h(_token(client, "bulk-t1@x.example.com"))
    h2 = _h(_token(client, "bulk-t2@x.example.com"))
    mine = _mk(client, h1, "Mine")
    theirs = _mk(client, h2, "Theirs")
    res = client.patch(
        "/api/items/bulk",
        headers=h1,
        json={"ids": [mine["id"], theirs["id"]], "fields": {"uom": "kg"},
              "fields_set": ["uom"]},
    ).json()
    by_id = {r["id"]: r for r in res["rows"]}
    assert by_id[mine["id"]]["result"] == "changed"
    assert by_id[theirs["id"]]["result"] == "error"
    assert res["errors"] == 1
    # theirs untouched
    assert client.get(f"/api/items/{theirs['id']}", headers=h2).json()["uom"] is None


def _first_category(client: TestClient, h: dict[str, str]) -> dict:
    cats = client.get("/api/item-categories", headers=h).json()
    assert cats, "register should have seeded categories"
    return cats[0]


def _make_group(client: TestClient, h: dict[str, str], name: str) -> dict:
    """Create a group, tolerating a seed collision by reusing the existing one."""
    r = client.post("/api/item-groups", headers=h, json={"name": name, "item_type": "bulk"})
    if r.status_code == 201:
        return r.json()
    assert r.status_code == 409
    return next(g for g in client.get("/api/item-groups", headers=h).json() if g["name"] == name)


def test_bulk_set_category_only(client: TestClient) -> None:
    h = _h(_token(client, "bulk-cat@x.example.com"))
    cat = _first_category(client, h)
    a = _mk(client, h, "Loose Item A")
    b = _mk(client, h, "Loose Item B")
    res = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [a["id"], b["id"]], "fields": {"category_id": cat["id"]},
              "fields_set": ["category_id"]},
    ).json()
    assert res["changed"] == 2
    assert client.get(f"/api/items/{a['id']}", headers=h).json()["category_id"] == cat["id"]


def test_bulk_remove_from_group(client: TestClient) -> None:
    h = _h(_token(client, "bulk-rmg@x.example.com"))
    g = _make_group(client, h, "Bulk-Test Group")
    it = _mk(client, h, "Grouped Item 3L", group_id=g["id"])
    assert client.get(f"/api/items/{it['id']}", headers=h).json()["group_id"] == g["id"]
    res = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [it["id"]], "fields": {"group_id": None}, "fields_set": ["group_id"]},
    ).json()
    assert res["changed"] == 1
    assert client.get(f"/api/items/{it['id']}", headers=h).json()["group_id"] is None


def test_bulk_recategorize_teaches_classify_rules(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_token(client, "bulk-7@x.example.com"))
    g = client.post(
        "/api/item-groups", headers=h, json={"name": "Kadai", "item_type": "bulk"}
    ).json()
    a = _mk(client, h, "SS Kadai 240")
    b = _mk(client, h, "SS Kadai 260")
    res = client.patch(
        "/api/items/bulk",
        headers=h,
        json={"ids": [a["id"], b["id"]], "fields": {"group_id": g["id"]},
              "fields_set": ["group_id"]},
    ).json()
    assert res["changed"] == 2
    # both items were unconfirmed and share the phrase "kadai" -> one deduped rule
    assert len(res["learned_rule_ids"]) >= 1
    rules = session.scalars(
        select(ItemClassifyRule).where(ItemClassifyRule.group_id == g["id"])
    ).all()
    assert any("kadai" in r.phrase_normalized for r in rules)


# --------------------------------------------------------------------------
# bulk delete
# --------------------------------------------------------------------------


def test_bulk_delete_unbilled(client: TestClient) -> None:
    h = _h(_token(client, "bd-1@x.example.com"))
    a = _mk(client, h, "Del A")
    b = _mk(client, h, "Del B")
    dry = client.post(
        "/api/items/bulk-delete?dry_run=true", headers=h, json={"ids": [a["id"], b["id"]]}
    ).json()
    assert dry["deleted"] == 2 and dry["dry_run"] is True
    assert client.get(f"/api/items/{a['id']}", headers=h).status_code == 200  # not gone yet

    real = client.post(
        "/api/items/bulk-delete", headers=h, json={"ids": [a["id"], b["id"]]}
    ).json()
    assert real["deleted"] == 2
    assert client.get(f"/api/items/{a['id']}", headers=h).status_code == 404


def test_bulk_delete_blocked_and_archive(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_token(client, "bd-2@x.example.com"))
    free = _mk(client, h, "Free Item")
    billed = _mk(client, h, "Billed Item")
    # simulate a document reference
    session.get(Item, billed["id"]).times_billed = 3
    session.commit()

    skip = client.post(
        "/api/items/bulk-delete", headers=h,
        json={"ids": [free["id"], billed["id"]], "on_blocked": "skip"},
    ).json()
    by_id = {r["id"]: r for r in skip["rows"]}
    assert by_id[free["id"]]["result"] == "deleted"
    assert by_id[billed["id"]]["result"] == "blocked"
    assert client.get(f"/api/items/{billed['id']}", headers=h).status_code == 200

    arch = client.post(
        "/api/items/bulk-delete", headers=h,
        json={"ids": [billed["id"]], "on_blocked": "archive"},
    ).json()
    assert arch["archived"] == 1
    assert client.get(f"/api/items/{billed['id']}", headers=h).json()["status"] == "archived"

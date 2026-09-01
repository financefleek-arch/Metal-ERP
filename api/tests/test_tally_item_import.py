"""Tally stock-items import: parser, zero-history skip, GUID→name→create
match ladder, HSN seeding on commit, re-import is a no-op.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import HsnCode
from tools.tally_import.parser import is_zero_history_dummy, parse_stock_items

FIXTURE = Path(__file__).parent / "fixtures" / "tally_stock_items_sample.xml"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _seed_hsn(session) -> None:  # type: ignore[no-untyped-def]
    # 72193590 known; 73239390 known; 76151010 (Hawkins) NOT seeded -> "bad_hsn"
    for code, desc, ch, rate in (
        ("72193590", "Flat-rolled stainless steel", "72", 18.0),
        ("73239390", "SS household articles", "73", 12.0),
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


def _upload(client: TestClient, h: dict[str, str], *, seed_all_hsn: bool = False):
    qs = "?seed_all_hsn=true" if seed_all_hsn else ""
    with FIXTURE.open("rb") as fh:
        return client.post(
            f"/api/items/import{qs}", headers=h, files={"file": ("stock.xml", fh, "text/xml")}
        )


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def test_parser_reads_stock_items_and_groups() -> None:
    s = parse_stock_items(FIXTURE.read_bytes())
    names = {i.name for i in s.items}
    assert {"SS 304 Patta 4in 2mm", "ST Storage Box 12X18", "Hawkins Cooker 5L"} <= names
    patta = next(i for i in s.items if i.name == "SS 304 Patta 4in 2mm")
    assert patta.base_units == "Kg"
    assert patta.hsn == "72193590"
    assert patta.gst_rate == 18.0
    assert patta.standard_rate == 268.0
    assert {g.name for g in s.groups} == {"Stainless Steel", "Utensils"}


def test_zero_history_dummy_detection() -> None:
    s = parse_stock_items(FIXTURE.read_bytes())
    dummy = next(i for i in s.items if i.name == "Rounding Off")
    real = next(i for i in s.items if i.name == "ST Storage Box 12X18")
    assert is_zero_history_dummy(dummy) is True
    assert is_zero_history_dummy(real) is False


# --------------------------------------------------------------------------
# endpoint flow
# --------------------------------------------------------------------------


def test_upload_skips_dummies_and_stages_the_rest(client: TestClient) -> None:
    h = _h(_token(client, "ii-1@x.example.com"))
    r = _upload(client, h)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["total"] == 3 and b["dummies_skipped"] == 1
    assert {g["name"] for g in b["groups"]} == {"Stainless Steel", "Utensils"}


def test_review_classifies_new_and_flags_unknown_hsn(client: TestClient) -> None:
    h = _h(_token(client, "ii-2@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    rev = client.get(f"/api/items/import/{batch}", headers=h).json()
    by_name = {r["stock_name"]: r for r in rev["rows"]}

    # empty catalogue -> everything "new"; Hawkins HSN is unseen -> flagged
    assert rev["counts"]["new"] == 2
    assert rev["counts"]["flag"] == 1
    assert by_name["Hawkins Cooker 5L"]["outcome"] == "flag"
    assert any(f["code"] == "bad_hsn" for f in by_name["Hawkins Cooker 5L"]["flags"])
    # Nos -> MRP
    assert by_name["Hawkins Cooker 5L"]["item_type"] == "mrp"
    assert by_name["ST Storage Box 12X18"]["item_type"] == "bulk"


def test_commit_creates_items_with_import_source(client: TestClient) -> None:
    h = _h(_token(client, "ii-3@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    out = client.post(f"/api/items/import/{batch}/commit", headers=h).json()
    # 2 ready (new), Hawkins is flagged
    assert out["created"] == 2 and out["still_flagged"] == 1

    items = client.get("/api/items", headers=h).json()
    patta = next(i for i in items if i["name"] == "SS 304 Patta 4in 2mm")
    assert patta["source"] == "import" and patta["status"] == "unconfirmed"
    full = client.get(f"/api/items/{patta['id']}", headers=h).json()
    assert full["hsn_code"] == "72193590"
    assert float(full["gst_rate"]) == 18.0
    assert full["default_rate"] == "268.00"


def test_commit_builds_groups_from_stock_groups(client: TestClient) -> None:
    h = _h(_token(client, "ii-grp@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    out = client.post(f"/api/items/import/{batch}/commit", headers=h).json()
    # "Stainless Steel" and "Utensils" Tally stock groups -> 2 product groups
    assert out["groups_created"] == 2

    # the tree now places the imported items, not "Uncategorised/Ungrouped"
    tree = client.get("/api/items/tree", headers=h).json()
    all_group_names = {
        g["name"] for cat in tree for g in cat["groups"]
    }
    assert {"Stainless Steel", "Utensils"} <= all_group_names

    patta = next(
        i for i in client.get("/api/items", headers=h).json()
        if i["name"] == "SS 304 Patta 4in 2mm"
    )
    full = client.get(f"/api/items/{patta['id']}", headers=h).json()
    assert full["group_id"] is not None
    # "Stainless Steel" stock group → matched the seeded "Stainless" category
    grp = client.get(f"/api/item-groups/{full['group_id']}", headers=h).json()
    assert grp["category_name"] == "Stainless"


def test_flagged_hsn_row_can_seed_and_commit(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_token(client, "ii-4@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    rev = client.get(f"/api/items/import/{batch}", headers=h).json()
    hawk = next(r for r in rev["rows"] if r["stock_name"] == "Hawkins Cooker 5L")

    # clear the flag by editing the name? no — the flag is bad_hsn. opt into seeding.
    client.patch(
        f"/api/items/import/{batch}/rows/{hawk['id']}",
        headers=h,
        json={"seed_hsn": True, "decision": "create"},
    )
    out = client.post(f"/api/items/import/{batch}/commit", headers=h).json()
    assert out["hsn_seeded"] == 1
    assert out["created"] == 3

    seeded = session.scalar(select(HsnCode).where(HsnCode.code == "76151010"))
    assert seeded is not None and float(seeded.default_gst_rate) == 12.0


def test_reimport_same_file_is_a_noop(client: TestClient) -> None:
    h = _h(_token(client, "ii-5@x.example.com"))
    b1 = _upload(client, h).json()["batch_id"]
    # commit the 2 clean ones + seed the hawkins hsn so all 3 land
    rev = client.get(f"/api/items/import/{b1}", headers=h).json()
    hawk = next(r for r in rev["rows"] if r["stock_name"] == "Hawkins Cooker 5L")
    client.patch(
        f"/api/items/import/{b1}/rows/{hawk['id']}",
        headers=h,
        json={"seed_hsn": True, "decision": "create"},
    )
    client.post(f"/api/items/import/{b1}/commit", headers=h)

    # second upload: GUIDs now match, nothing blank to fill -> all "skip"
    b2 = _upload(client, h).json()["batch_id"]
    rev2 = client.get(f"/api/items/import/{b2}", headers=h).json()
    assert rev2["counts"]["skip"] == 3
    out2 = client.post(f"/api/items/import/{b2}/commit", headers=h).json()
    assert out2["created"] == 0 and out2["updated"] == 0 and out2["skipped"] == 3


def test_second_import_links_by_name_and_fills_blanks(client: TestClient) -> None:
    h = _h(_token(client, "ii-6@x.example.com"))
    # hand-create an item with the same normalized name, no HSN / no rate
    made = client.post(
        "/api/items", headers=h, json={"name": "SS 304 Patta 4in 2mm"}
    )
    assert made.status_code == 201
    pid = made.json()["id"]

    batch = _upload(client, h).json()["batch_id"]
    rev = client.get(f"/api/items/import/{batch}", headers=h).json()
    patta_row = next(r for r in rev["rows"] if r["stock_name"] == "SS 304 Patta 4in 2mm")
    assert patta_row["outcome"] == "link"
    assert patta_row["match_item_id"] == pid

    out = client.post(f"/api/items/import/{batch}/commit", headers=h).json()
    assert out["updated"] == 1

    full = client.get(f"/api/items/{pid}", headers=h).json()
    assert full["hsn_code"] == "72193590"        # filled
    assert full["default_rate"] == "268.00"      # filled
    assert full["uom"] == "kg"                   # filled


def test_discard_batch(client: TestClient) -> None:
    h = _h(_token(client, "ii-7@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    assert client.delete(f"/api/items/import/{batch}", headers=h).status_code == 204
    assert client.get(f"/api/items/import/{batch}", headers=h).status_code == 404


def test_upload_rejects_junk(client: TestClient) -> None:
    h = _h(_token(client, "ii-8@x.example.com"))
    r = client.post(
        "/api/items/import", headers=h, files={"file": ("j.xml", b"not xml", "text/xml")}
    )
    assert r.status_code == 422


def test_seed_all_hsn_arms_every_hsn_row(client: TestClient) -> None:
    h = _h(_token(client, "ii-seedall@x.example.com"))
    batch = _upload(client, h, seed_all_hsn=True).json()["batch_id"]
    rev = client.get(f"/api/items/import/{batch}", headers=h).json()
    # Hawkins HSN is unseeded -> would normally flag; seed_all_hsn clears it
    assert rev["counts"]["flag"] == 0
    hawk = next(r for r in rev["rows"] if r["stock_name"] == "Hawkins Cooker 5L")
    assert hawk["seed_hsn"] is True
    assert hawk["outcome"] == "new"

    out = client.post(f"/api/items/import/{batch}/commit", headers=h).json()
    assert out["created"] == 3
    assert out["still_flagged"] == 0
    assert out["hsn_seeded"] == 1  # only Hawkins' code was missing


def test_reupload_discards_prior_uncommitted_batch(client: TestClient) -> None:
    h = _h(_token(client, "ii-reup@x.example.com"))
    b1 = _upload(client, h).json()["batch_id"]
    # do NOT commit b1
    b2 = _upload(client, h).json()["batch_id"]
    assert b2 != b1
    # b1's staging rows are gone
    assert client.get(f"/api/items/import/{b1}", headers=h).status_code == 404
    # b2 is intact
    assert client.get(f"/api/items/import/{b2}", headers=h).status_code == 200


def test_reupload_keeps_committed_batch(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_token(client, "ii-keep@x.example.com"))
    b1 = _upload(client, h, seed_all_hsn=True).json()["batch_id"]
    out = client.post(f"/api/items/import/{b1}/commit", headers=h).json()
    assert out["created"] == 3
    # a later upload must NOT wipe the committed audit rows
    _upload(client, h, seed_all_hsn=True)
    assert client.get(f"/api/items/import/{b1}", headers=h).status_code == 200

    from app.models import StagingTallyItem

    session.expire_all()
    b1_rows = session.scalars(
        select(StagingTallyItem).where(StagingTallyItem.batch_id == b1)
    ).all()
    assert len(b1_rows) == 3
    assert all(r.committed_as is not None for r in b1_rows)


# --------------------------------------------------------------------------
# real full-catalogue "All Masters" export (SETHIA METAL STORE, ~2094 items)
# --------------------------------------------------------------------------

_REAL_MASTER = Path(r"C:\tmp\Master (3).xml")


@pytest.mark.skipif(not _REAL_MASTER.exists(), reason="real Tally export not present")
def test_real_all_masters_export_imports(client: TestClient) -> None:
    h = _h(_token(client, "ii-real@x.example.com"))

    with _REAL_MASTER.open("rb") as fh:
        r = client.post(
            "/api/items/import?seed_all_hsn=true",
            headers=h,
            files={"file": ("Master (3).xml", fh, "text/xml")},
        )
    assert r.status_code == 201, r.text[:500]
    b = r.json()
    assert b["total"] > 2000
    assert b["dummies_skipped"] == 0

    rev = client.get(f"/api/items/import/{b['batch_id']}", headers=h).json()
    codes = {c for row in rev["rows"] for f in row["flags"] for c in [f["code"]]}
    # item names carry * % + : _ as size / SKU notation -> no name-shape flag
    assert "name_bad_chars" not in codes

    # SQLite won't reject over-length varchars, so assert the parsed_* hints
    # fit their Postgres column widths (regression: parsed_shape overflowed 24).
    for row in rev["rows"]:
        p = row["parsed"]
        assert p["metal"] is None or len(p["metal"]) <= 20
        assert p["shape"] is None or len(p["shape"]) <= 24
        assert p["size_text"] is None or len(p["size_text"]) <= 60
        assert p["sku"] is None or len(p["sku"]) <= 64
    # bad_hsn may still be *listed* on a row, but seed_all_hsn clears it as a
    # blocker, so nothing is flagged and the whole file is "new".
    flagged = [r for r in rev["rows"] if r["outcome"] == "flag"]
    assert flagged == [], [r["stock_name"] for r in flagged[:20]]
    assert rev["counts"]["new"] > 2000

    out = client.post(f"/api/items/import/{b['batch_id']}/commit", headers=h).json()
    assert out["created"] > 2000
    assert out["still_flagged"] == 0
    assert out["hsn_seeded"] > 0

    items = client.get("/api/items", headers=h).json()
    assert len(items) > 2000

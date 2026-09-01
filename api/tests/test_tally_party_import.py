"""Tally party import: parser, group→role, match ladder, review, commit.

The parser/groups tests are pure. The endpoint tests run against the
conftest SQLite DB (name-fuzzy match degrades to exact-normalised there,
which the tests account for).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models._mixins import PartyRole
from tools.tally_import.groups import GroupTree
from tools.tally_import.parser import parse_masters

FIXTURE = Path(__file__).parent / "fixtures" / "tally_masters_sample.xml"


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def test_parser_reads_ledgers_and_groups() -> None:
    m = parse_masters(FIXTURE.read_bytes())
    names = {led.name for led in m.ledgers}
    assert {"Balaji Traders", "Metro Steel Corp", "Nilkanth Hardware", "Cash", "CGST"} <= names
    bal = next(led for led in m.ledgers if led.name == "Balaji Traders")
    assert bal.gstin == "19AAJCB1234K1ZM"
    assert bal.pan == "AAJCB1234K"
    assert bal.state_name == "West Bengal"
    assert bal.phone == "9832011223"
    assert bal.address_lines == ["Sevoke Road", "Ward 42", "Siliguri"]
    assert bal.pincode == "734001"
    group_names = {g.name for g in m.groups}
    assert {"Sundry Debtors", "Sundry Creditors", "Local", "Steel Cos"} <= group_names


def test_parser_handles_utf16_and_bom() -> None:
    raw = FIXTURE.read_bytes()
    as16 = ('<?xml version="1.0" encoding="UTF-16"?>' + raw.decode("utf-8")).encode("utf-16")
    m = parse_masters(as16)
    assert any(led.name == "Balaji Traders" for led in m.ledgers)


def test_parser_strips_illegal_control_entities() -> None:
    raw = FIXTURE.read_bytes().replace(b"Sevoke Road", b"Sevoke&#4; Road")
    m = parse_masters(raw)
    bal = next(led for led in m.ledgers if led.name == "Balaji Traders")
    assert "Sevoke Road" in bal.address_lines[0]


# --------------------------------------------------------------------------
# group -> role
# --------------------------------------------------------------------------


def test_group_tree_resolves_roles() -> None:
    m = parse_masters(FIXTURE.read_bytes())
    tree = GroupTree(m.groups)
    assert tree.role_for("Local") is PartyRole.customer
    assert tree.role_for("Steel Cos") is PartyRole.supplier
    assert tree.role_for("Sundry Debtors") is PartyRole.customer
    assert tree.role_for("Cash-in-Hand") is None
    assert tree.role_for("Duties & Taxes") is None


# --------------------------------------------------------------------------
# endpoint flow
# --------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _token(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _upload(client: TestClient, h: dict[str, str]):
    with FIXTURE.open("rb") as fh:
        return client.post(
            "/api/parties/import",
            headers=h,
            files={"file": ("masters.xml", fh, "text/xml")},
        )


def test_upload_stages_only_trade_parties(client: TestClient) -> None:
    h = _h(_token(client, "imp-1@x.example.com"))
    r = _upload(client, h)
    assert r.status_code == 201, r.text
    body = r.json()
    # Balaji, Metro Steel, Nilkanth -> staged. Cash / CGST / Transport -> not.
    assert body["total"] == 3
    grp_names = {g["name"] for g in body["groups"]}
    assert "sundry debtors" in grp_names and "sundry creditors" in grp_names


def test_review_classifies_rows(client: TestClient) -> None:
    h = _h(_token(client, "imp-2@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]

    rev = client.get(f"/api/parties/import/{batch}", headers=h).json()
    by_name = {row["ledger_name"]: row for row in rev["rows"]}

    # No existing parties yet -> all three are "new".
    assert rev["counts"]["new"] == 3
    assert by_name["Metro Steel Corp"]["role"] == "supplier"
    assert by_name["Balaji Traders"]["role"] == "customer"
    assert "address" in by_name["Metro Steel Corp"]["missing"]


def test_commit_creates_parties_with_tally_source(client: TestClient) -> None:
    h = _h(_token(client, "imp-3@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]

    out = client.post(f"/api/parties/import/{batch}/commit", headers=h).json()
    assert out["created"] == 3 and out["updated"] == 0

    parties = client.get("/api/parties", headers=h).json()
    bal = next(p for p in parties if p["legal_name"] == "Balaji Traders")
    assert bal["source"] == "tally_import"
    assert bal["source_ref"] == "a1b2c3d4-0001"

    full = client.get(f"/api/parties/{bal['id']}", headers=h).json()
    assert full["gstin"] == "19AAJCB1234K1ZM"
    assert full["default_state_code"] == "19"
    assert full["addresses"][0]["line1"] == "Sevoke Road"
    assert full["addresses"][0]["pincode"] == "734001"


def test_second_import_links_by_gstin_and_fills_blanks(client: TestClient) -> None:
    h = _h(_token(client, "imp-4@x.example.com"))

    # An existing party with the same GSTIN but no phone/address.
    created = client.post(
        "/api/parties",
        headers=h,
        json={"legal_name": "Balaji Traders", "gstin": "19aajcb1234k1zm", "role": "customer"},
    )
    assert created.status_code == 201
    pid = created.json()["id"]

    batch = _upload(client, h).json()["batch_id"]
    rev = client.get(f"/api/parties/import/{batch}", headers=h).json()
    bal_row = next(r for r in rev["rows"] if r["ledger_name"] == "Balaji Traders")
    assert bal_row["outcome"] == "link"
    assert bal_row["match_party_id"] == pid

    out = client.post(f"/api/parties/import/{batch}/commit", headers=h).json()
    assert out["updated"] == 1
    # Balaji linked+filled; Metro + Nilkanth still created.
    assert out["created"] == 2

    full = client.get(f"/api/parties/{pid}", headers=h).json()
    assert full["phone"] == "+919832011223"  # filled from the ledger
    assert full["addresses"][0]["line1"] == "Sevoke Road"


def test_gstin_match_widens_role_to_both(client: TestClient) -> None:
    h = _h(_token(client, "imp-5@x.example.com"))
    # Existing SUPPLIER with Balaji's GSTIN; the ledger is under Debtors (customer).
    created = client.post(
        "/api/parties",
        headers=h,
        json={"legal_name": "Balaji Traders", "gstin": "19aajcb1234k1zm", "role": "supplier"},
    )
    pid = created.json()["id"]
    batch = _upload(client, h).json()["batch_id"]
    client.post(f"/api/parties/import/{batch}/commit", headers=h)
    assert client.get(f"/api/parties/{pid}", headers=h).json()["role"] == "both"


def test_flagged_row_excluded_until_resolved(client: TestClient) -> None:
    h = _h(_token(client, "imp-6@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    rev = client.get(f"/api/parties/import/{batch}", headers=h).json()
    metro = next(r for r in rev["rows"] if r["ledger_name"] == "Metro Steel Corp")

    # Force a flag by shortening nothing but marking skip, then re-including.
    client.patch(
        f"/api/parties/import/{batch}/rows/{metro['id']}",
        headers=h,
        json={"decision": "skip"},
    )
    out = client.post(f"/api/parties/import/{batch}/commit", headers=h).json()
    assert out["skipped"] == 1
    assert out["created"] == 2
    assert "Metro Steel Corp" not in {
        p["legal_name"] for p in client.get("/api/parties", headers=h).json()
    }


def test_discard_batch(client: TestClient) -> None:
    h = _h(_token(client, "imp-7@x.example.com"))
    batch = _upload(client, h).json()["batch_id"]
    assert client.delete(f"/api/parties/import/{batch}", headers=h).status_code == 204
    assert client.get(f"/api/parties/import/{batch}", headers=h).status_code == 404


def test_upload_rejects_non_xml(client: TestClient) -> None:
    h = _h(_token(client, "imp-8@x.example.com"))
    r = client.post(
        "/api/parties/import",
        headers=h,
        files={"file": ("junk.xml", b"not xml at all", "text/xml")},
    )
    assert r.status_code == 422

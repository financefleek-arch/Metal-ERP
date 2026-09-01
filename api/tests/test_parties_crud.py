"""Parties CRUD slice: status/archive, provenance, completeness, search, dormancy.

Runs against the conftest SQLite DB. Trigram ranking is Postgres-only; on
SQLite the search falls back to substring across name/address/phone, which
these tests exercise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Party, Tenant


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mk(client: TestClient, h: dict[str, str], name: str, **extra: object) -> dict:
    r = client.post("/api/parties", headers=h, json={"legal_name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# provenance + completeness on the read payloads
# --------------------------------------------------------------------------


def test_new_party_defaults_and_completeness(client: TestClient) -> None:
    h = _auth(_register(client, "prov@x.example.com"))

    bare = _mk(client, h, "Name Only Co")
    assert bare["source"] == "manual"
    assert bare["source_ref"] is None
    assert bare["status"] == "active"
    assert bare["last_txn_at"] is None
    assert bare["document_count"] == 0
    assert bare["completeness"] == {"complete": False, "missing": ["address"]}

    full = _mk(
        client,
        h,
        "Complete Co",
        addresses=[
            {
                "type": "both",
                "line1": "Sevoke Road",
                "city": "Siliguri",
                "state_code": "19",
                "is_default": True,
            }
        ],
    )
    assert full["completeness"] == {"complete": True, "missing": []}

    # partial address -> specific missing tokens
    partial = _mk(
        client,
        h,
        "Partial Co",
        addresses=[{"type": "both", "line1": "Somewhere", "is_default": True}],
    )
    assert partial["completeness"]["complete"] is False
    assert set(partial["completeness"]["missing"]) == {"address_city", "address_state"}


def test_incomplete_filter(client: TestClient) -> None:
    h = _auth(_register(client, "incomp@x.example.com"))
    _mk(client, h, "Missing Addr Co")
    _mk(
        client,
        h,
        "Has Addr Co",
        addresses=[
            {"type": "both", "line1": "X", "city": "Siliguri", "state_code": "19"}
        ],
    )

    names = {p["legal_name"] for p in client.get("/api/parties", headers=h).json()}
    assert names == {"Missing Addr Co", "Has Addr Co"}

    incomplete = client.get("/api/parties?completeness=incomplete", headers=h).json()
    assert [p["legal_name"] for p in incomplete] == ["Missing Addr Co"]


# --------------------------------------------------------------------------
# status / archive
# --------------------------------------------------------------------------


def test_archive_hides_from_default_list(client: TestClient) -> None:
    h = _auth(_register(client, "arch@x.example.com"))
    p = _mk(client, h, "To Archive Co")

    upd = client.patch(f"/api/parties/{p['id']}", headers=h, json={"status": "archived"})
    assert upd.status_code == 200
    assert upd.json()["status"] == "archived"

    # default list excludes archived
    assert client.get("/api/parties", headers=h).json() == []
    # explicit filter shows it
    arch = client.get("/api/parties?status=archived", headers=h).json()
    assert [x["legal_name"] for x in arch] == ["To Archive Co"]
    # unarchive
    client.patch(f"/api/parties/{p['id']}", headers=h, json={"status": "active"})
    assert len(client.get("/api/parties", headers=h).json()) == 1


# --------------------------------------------------------------------------
# delete guard (document_count is 0 until Sales/Inward land, so hard delete
# always succeeds here; the 409 path is covered once those FKs exist)
# --------------------------------------------------------------------------


def test_delete_unreferenced_party_succeeds(client: TestClient) -> None:
    h = _auth(_register(client, "del@x.example.com"))
    p = _mk(client, h, "Deletable Co")
    assert client.delete(f"/api/parties/{p['id']}", headers=h).status_code == 204
    assert client.get(f"/api/parties/{p['id']}", headers=h).status_code == 404


# --------------------------------------------------------------------------
# rename dup guard on PATCH (new — POST already guarded)
# --------------------------------------------------------------------------


def test_patch_rename_to_existing_name_conflicts(client: TestClient) -> None:
    h = _auth(_register(client, "rename@x.example.com"))
    _mk(client, h, "Alpha Traders")
    b = _mk(client, h, "Beta Traders")

    r = client.patch(
        f"/api/parties/{b['id']}", headers=h, json={"legal_name": "alpha traders"}
    )
    assert r.status_code == 409

    # renaming to a genuinely new name is fine
    ok = client.patch(
        f"/api/parties/{b['id']}", headers=h, json={"legal_name": "Gamma Traders"}
    )
    assert ok.status_code == 200 and ok.json()["legal_name"] == "Gamma Traders"


# --------------------------------------------------------------------------
# search across name / address / phone
# --------------------------------------------------------------------------


def test_search_name_address_phone(client: TestClient) -> None:
    h = _auth(_register(client, "search@x.example.com"))
    _mk(
        client,
        h,
        "Balaji Traders",
        phone="+91 98321 37599",
        addresses=[
            {"type": "both", "line1": "Sevoke Road", "city": "Siliguri", "state_code": "19"}
        ],
    )
    _mk(
        client,
        h,
        "Rajdeep Stores",
        phone="9800000000",
        addresses=[
            {"type": "both", "line1": "Hill Cart Road", "city": "Siliguri", "state_code": "19"}
        ],
    )

    # by name substring
    r = client.get("/api/parties?q=balaji", headers=h).json()
    assert [p["legal_name"] for p in r] == ["Balaji Traders"]

    # by address token
    r = client.get("/api/parties?q=sevoke", headers=h).json()
    assert [p["legal_name"] for p in r] == ["Balaji Traders"]

    # by phone digits, ignoring formatting in the stored value
    r = client.get("/api/parties?q=9832", headers=h).json()
    assert [p["legal_name"] for p in r] == ["Balaji Traders"]

    # shared city token returns both
    r = client.get("/api/parties?q=siliguri", headers=h).json()
    assert {p["legal_name"] for p in r} == {"Balaji Traders", "Rajdeep Stores"}


# --------------------------------------------------------------------------
# dormancy
# --------------------------------------------------------------------------


def test_dormant_filter(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _auth(_register(client, "dorm@x.example.com"))
    recent = _mk(client, h, "Recently Billed Co")
    stale = _mk(client, h, "Long Gone Co")
    never_new = _mk(client, h, "Fresh Never Billed Co")

    now = datetime.now(UTC)
    r = session.scalar(select(Party).where(Party.id == recent["id"]))
    r.last_txn_at = now - timedelta(days=10)
    s = session.scalar(select(Party).where(Party.id == stale["id"]))
    s.last_txn_at = now - timedelta(days=400)
    # make the never-billed one look freshly created so it is NOT dormant
    n = session.scalar(select(Party).where(Party.id == never_new["id"]))
    n.created_at = now - timedelta(days=3)
    session.commit()

    dormant = client.get("/api/parties?dormant=true", headers=h).json()
    assert [p["legal_name"] for p in dormant] == ["Long Gone Co"]


def test_dormant_window_is_tenant_configurable(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _auth(_register(client, "dwin@x.example.com"))
    p = _mk(client, h, "Ninety Day Co")
    party = session.scalar(select(Party).where(Party.id == p["id"]))
    party.last_txn_at = datetime.now(UTC) - timedelta(days=100)
    tenant = session.scalar(select(Tenant).where(Tenant.id == party.tenant_id))
    tenant.dormant_party_days = 90
    session.commit()

    assert [x["legal_name"] for x in client.get("/api/parties?dormant=true", headers=h).json()] == [
        "Ninety Day Co"
    ]

    tenant.dormant_party_days = 365
    session.commit()
    assert client.get("/api/parties?dormant=true", headers=h).json() == []

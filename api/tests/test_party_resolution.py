"""`resolve_party` ladder + the `/api/parties/resolve` endpoint.

Runs against the conftest SQLite DB, so the trigram (fuzzy) rung is skipped —
steps 1-3 (GSTIN / phone / exact normalized-name key) are what's exercised
here. `_register` seeds the synonym dictionary, including the party legal-
suffix drops ("pvt"/"ltd"/... -> ""), so "Steel Traders Pvt. Ltd." and
"STEEL TRADERS PRIVATE LIMITED" share a de-dup key.

The Postgres-only fuzzy cases (weak / ambiguous candidates, `?force=true`
bypass) live in test_parties_crud.py behind a skipif and in CI's PG run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.normalize import load_synonym_map, normalize_name
from app.main import app
from app.services.party_resolution import resolve_party


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str) -> tuple[dict[str, str], str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    tid = client.get("/api/auth/me", headers=h).json()["tenant_id"]
    return h, tid


def _mk(client: TestClient, h: dict[str, str], name: str, **extra: object) -> dict:
    r = client.post("/api/parties", headers=h, json={"legal_name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# normalize_name — the de-dup key
# --------------------------------------------------------------------------


def test_key_collapses_company_suffix_and_case(client: TestClient) -> None:
    _, tid = _register(client, "key1@x.example.com")
    from app.db import SessionLocal

    with SessionLocal() as s:
        syn = load_synonym_map(s, tid)
    a = normalize_name("Steel Traders Pvt. Ltd.", syn)
    b = normalize_name("STEEL TRADERS PRIVATE LIMITED", syn)
    assert a == b == "steel traders"


def test_key_collapses_whitespace_and_punctuation(client: TestClient) -> None:
    _, tid = _register(client, "key2@x.example.com")
    from app.db import SessionLocal

    with SessionLocal() as s:
        syn = load_synonym_map(s, tid)
    assert normalize_name("Steel   Traders", syn) == normalize_name("steel traders", syn)
    # punctuation -> space, case-folded, collapsed
    assert normalize_name("Bharat-Steel  Corp.", syn) == "bharat steel corp"
    # "and" is dropped as a suffix-noise token
    assert normalize_name("Bharat Iron and Steel", syn) == "bharat iron steel"


# --------------------------------------------------------------------------
# resolve_party — hard keys + exact
# --------------------------------------------------------------------------


def test_exact_key_rung(client: TestClient) -> None:
    h, tid = _register(client, "exact@x.example.com")
    made = _mk(client, h, "Steel Traders")
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "Steel  Traders")
    assert m.method == "exact"
    assert m.confidence == 1.0
    assert m.party_id == made["id"]


def test_gstin_rung_beats_name(client: TestClient) -> None:
    h, tid = _register(client, "gstin@x.example.com")
    made = _mk(client, h, "Alpha Metals", gstin="19BHBPK1450P1Z3")
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "Totally Different Name", gstin="19bhbpk1450p1z3")
    assert m.method == "gstin"
    assert m.party_id == made["id"]


def test_phone_rung_digits_only(client: TestClient) -> None:
    h, tid = _register(client, "phone@x.example.com")
    made = _mk(client, h, "Beta Steel", phone="9812345678")
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "Some Other Firm", phone="+91 98123-45678")
    assert m.method == "phone"
    assert m.party_id == made["id"]


def test_exclude_id_skips_self(client: TestClient) -> None:
    h, tid = _register(client, "self@x.example.com")
    made = _mk(client, h, "Gamma Traders")
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "Gamma Traders", exclude_id=made["id"])
    assert m.party_id is None
    assert m.method is None


def test_empty_name_no_hard_key_is_no_match(client: TestClient) -> None:
    _, tid = _register(client, "empty@x.example.com")
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "!!!  ---")
    assert m.party_id is None
    assert m.method is None
    assert m.candidates == []


def test_archived_party_not_matched(client: TestClient) -> None:
    h, tid = _register(client, "arch@x.example.com")
    made = _mk(client, h, "Delta Steel")
    r = client.delete(f"/api/parties/{made['id']}", headers=h)
    assert r.status_code == 204
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "Delta Steel")
    assert m.party_id is None


def test_sqlite_fuzzy_rung_skipped_no_crash(client: TestClient) -> None:
    h, tid = _register(client, "fuzz@x.example.com")
    _mk(client, h, "Steel Traders")
    from app.db import SessionLocal

    with SessionLocal() as s:
        m = resolve_party(s, tid, "Steel Tradrs")  # typo — would fuzzy-match on PG
    # SQLite: no pg_trgm, so step 4 is skipped and nothing comes back.
    assert m.party_id is None
    assert m.method is None
    assert m.candidates == []


# --------------------------------------------------------------------------
# POST /api/parties/resolve
# --------------------------------------------------------------------------


def test_resolve_endpoint_exact(client: TestClient) -> None:
    h, _ = _register(client, "ep1@x.example.com")
    made = _mk(client, h, "Steel Traders")
    r = client.post("/api/parties/resolve?name=steel%20%20traders", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "exact"
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["id"] == made["id"]


def test_resolve_endpoint_no_match(client: TestClient) -> None:
    h, _ = _register(client, "ep2@x.example.com")
    _mk(client, h, "Steel Traders")
    r = client.post("/api/parties/resolve?name=zzz%20nothing%20here", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["method"] is None
    assert body["candidates"] == []

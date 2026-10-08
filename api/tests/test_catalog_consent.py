"""Consent to be sent price lists on WhatsApp: recorded on the party, with when / how / who."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, Party
from app.services.catalog_consent import may_send_catalog


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _shop(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Consent Metals", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _party(client: TestClient, h: dict[str, str], name: str, **extra: object) -> dict:
    r = client.post("/api/parties", headers=h, json={"legal_name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_new_party_has_not_been_asked(client: TestClient) -> None:
    h = _shop(client, "c1@x.example.com")
    p = _party(client, h, "Ravi Traders", phone="9876543210")
    assert p["wa_catalog_consent"] == "none"
    assert p["wa_catalog_consent_at"] is None
    assert p["wa_catalog_consent_source"] is None


def test_staff_record_opt_in_then_opt_out(client: TestClient) -> None:
    h = _shop(client, "c2@x.example.com")
    p = _party(client, h, "Ravi Traders", phone="9876543210")

    r = client.put(
        f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "opted_in"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "opted_in"
    assert r.json()["source"] == "manual"
    assert r.json()["at"]
    with SessionLocal() as s:
        row = s.get(Party, p["id"])
        assert row.wa_catalog_consent_by  # who recorded it
        assert may_send_catalog(row)

    assert client.get(f"/api/parties/{p['id']}", headers=h).json()["wa_catalog_consent"] == (
        "opted_in"
    )

    r = client.put(
        f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "opted_out"}
    )
    assert r.json()["status"] == "opted_out"
    with SessionLocal() as s:
        assert not may_send_catalog(s.get(Party, p["id"]))
        trail = list(
            s.scalars(
                select(AuditLog).where(
                    AuditLog.entity_id == p["id"], AuditLog.action == "catalog_consent"
                )
            )
        )
        assert len(trail) == 2


def test_cannot_opt_in_without_a_phone(client: TestClient) -> None:
    h = _shop(client, "c3@x.example.com")
    p = _party(client, h, "No Phone Co")
    r = client.put(
        f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "opted_in"}
    )
    assert r.status_code == 422
    # opting out needs no phone
    r = client.put(
        f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "opted_out"}
    )
    assert r.status_code == 200


def test_none_cannot_be_set_by_hand(client: TestClient) -> None:
    h = _shop(client, "c4@x.example.com")
    p = _party(client, h, "Ravi Traders", phone="9876543210")
    r = client.put(f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "none"})
    assert r.status_code == 422


def test_repeating_the_same_answer_changes_nothing(client: TestClient) -> None:
    h = _shop(client, "c5@x.example.com")
    p = _party(client, h, "Ravi Traders", phone="9876543210")
    url = f"/api/parties/{p['id']}/catalog-consent"
    first = client.put(url, headers=h, json={"status": "opted_in"}).json()
    again = client.put(url, headers=h, json={"status": "opted_in"}).json()
    # SQLite hands back a naive datetime (no "Z"); the instant is what must not change
    assert first["at"].rstrip("Z") == again["at"].rstrip("Z")
    with SessionLocal() as s:
        n = len(
            list(
                s.scalars(
                    select(AuditLog).where(
                        AuditLog.entity_id == p["id"], AuditLog.action == "catalog_consent"
                    )
                )
            )
        )
        assert n == 1


def test_other_firms_party_is_not_reachable(client: TestClient) -> None:
    a = _shop(client, "c6a@x.example.com")
    b = _shop(client, "c6b@x.example.com")
    p = _party(client, a, "Ravi Traders", phone="9876543210")
    r = client.put(
        f"/api/parties/{p['id']}/catalog-consent", headers=b, json={"status": "opted_in"}
    )
    assert r.status_code == 404


def test_patching_a_party_never_touches_consent(client: TestClient) -> None:
    h = _shop(client, "c7@x.example.com")
    p = _party(client, h, "Ravi Traders", phone="9876543210")
    client.put(f"/api/parties/{p['id']}/catalog-consent", headers=h, json={"status": "opted_in"})
    r = client.patch(
        f"/api/parties/{p['id']}",
        headers=h,
        json={"wa_catalog_consent": "opted_out", "email": "r@x.example.com"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["wa_catalog_consent"] == "opted_in"

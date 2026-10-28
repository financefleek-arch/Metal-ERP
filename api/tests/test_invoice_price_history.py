"""Party price history: what this party was last billed for an item (Tally's Alt+S)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Invoice, Item


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str, firm: str = "Sethia Metal Store") -> dict[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": firm, "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _party(client: TestClient, h: dict, name: str) -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bill(client: TestClient, h: dict, pid: str, lines: list[dict], *, finalize: bool = True) -> str:
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(f"/api/invoices/{d['id']}", headers=h, json={"lines": lines})
    assert r.status_code == 200, r.text
    if finalize:
        r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
        assert r.status_code == 200, r.text
    return d["id"]


def _line(desc: str, rate: str, qty: str = "2", uom: str = "kg") -> dict:
    return {"description": desc, "quantity": qty, "uom": uom, "unit_rate": rate}


def _item_id(session, name: str) -> str:  # type: ignore[no-untyped-def]
    return session.scalar(select(Item.id).where(Item.name == name))


def test_history_is_per_party_and_newest_first(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _register(client, "ph1@x.example.com")
    sharma = _party(client, h, "Sharma Traders")
    gupta = _party(client, h, "Gupta Stores")

    _bill(client, h, sharma, [_line("Steel Glass", "420.00")])
    _bill(client, h, gupta, [_line("Steel Glass", "450.00")])
    _bill(client, h, sharma, [_line("Steel Glass", "430.00")])
    item = _item_id(session, "Steel Glass")

    r = client.get(
        "/api/invoices/price-history", headers=h, params={"party_id": sharma, "item_id": item}
    )
    assert r.status_code == 200, r.text
    rates = [row["unit_rate"] for row in r.json()["rows"]]
    assert rates == ["430.00", "420.00"]  # Gupta's 450 never shows; newest first
    assert all(row["source"] == "native" for row in r.json()["rows"])

    r = client.get(
        "/api/invoices/price-history", headers=h, params={"party_id": gupta, "item_id": item}
    )
    assert [row["unit_rate"] for row in r.json()["rows"]] == ["450.00"]


def test_drafts_are_not_history(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _register(client, "ph2@x.example.com")
    pid = _party(client, h, "Sharma Traders")
    _bill(client, h, pid, [_line("Steel Plate", "300.00")])
    item = _item_id(session, "Steel Plate")
    _bill(client, h, pid, [{**_line("Steel Plate", "999.00"), "item_id": item}], finalize=False)

    r = client.get(
        "/api/invoices/price-history", headers=h, params={"party_id": pid, "item_id": item}
    )
    assert [row["unit_rate"] for row in r.json()["rows"]] == ["300.00"]


def test_credit_notes_are_not_a_selling_price(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _register(client, "ph3@x.example.com")
    pid = _party(client, h, "Sharma Traders")
    iid = _bill(client, h, pid, [_line("Steel Bowl", "100.00")])
    item = _item_id(session, "Steel Bowl")
    inv = session.get(Invoice, iid)
    inv.doc_type = "crn"
    session.commit()

    r = client.get(
        "/api/invoices/price-history", headers=h, params={"party_id": pid, "item_id": item}
    )
    assert r.json()["rows"] == []


def test_last_prices_batch(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _register(client, "ph4@x.example.com")
    pid = _party(client, h, "Sharma Traders")
    _bill(client, h, pid, [_line("Steel Glass", "420.00"), _line("Steel Jug", "700.00")])
    _bill(client, h, pid, [_line("Steel Glass", "430.00")])
    glass = _item_id(session, "Steel Glass")
    jug = _item_id(session, "Steel Jug")
    never = "00000000-0000-0000-0000-000000000000"

    r = client.get(
        "/api/invoices/price-history/last",
        headers=h,
        params={"party_id": pid, "item_ids": f"{glass},{jug},{never}"},
    )
    assert r.status_code == 200, r.text
    got = {p["item_id"]: p["unit_rate"] for p in r.json()["prices"]}
    assert got == {glass: "430.00", jug: "700.00"}  # latest per item; unsold item absent


def test_other_firms_party_is_404(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    a = _register(client, "ph5a@x.example.com", "Firm A")
    b = _register(client, "ph5b@x.example.com", "Firm B")
    pid = _party(client, a, "Sharma Traders")

    r = client.get(
        "/api/invoices/price-history",
        headers=b,
        params={"party_id": pid, "item_id": "x"},
    )
    assert r.status_code == 404
    r = client.get(
        "/api/invoices/price-history/last", headers=b, params={"party_id": pid, "item_ids": "x"}
    )
    assert r.status_code == 404

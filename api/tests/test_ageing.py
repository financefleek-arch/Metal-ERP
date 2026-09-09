"""F3a — Collections ageing dashboard: bucketing, credit-days, opening
balance, overdue flag.

Setup goes through the API (register / party / invoice / payment), then a
direct session backdates `Invoice.date` / `opening_balance_as_of` — the
create API always stamps today's date, and ageing is all about the past.
Bucket keys: lt30 / d30 / d60 / d90p  ("<30 days / 30+ / 60+ / >3 months").
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import Invoice, Party, Tenant


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _register(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Ageing Test Co", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _party(client: TestClient, h: dict, name: str) -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _finalized_invoice(client: TestClient, h: dict, pid: str, rate: str) -> str:
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "SS Item", "quantity": "1", "unit_rate": rate}]},
    )
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text
    return d["id"]


def _backdate_invoice(session: Session, invoice_id: str, days_ago: int) -> None:
    inv = session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    assert inv is not None
    inv.date = date.today() - timedelta(days=days_ago)
    session.commit()


def _ageing(client: TestClient, h: dict, **params) -> list[dict]:
    r = client.get("/api/collections/ageing", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------


def test_buckets_by_invoice_age_credit_days_zero(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "age1@x.example.com"))
    pid = _party(client, h, "Multi Bucket Co")
    i_fresh = _finalized_invoice(client, h, pid, "100.00")   # 5d  -> lt30
    i_mid = _finalized_invoice(client, h, pid, "200.00")     # 45d -> d30
    i_old = _finalized_invoice(client, h, pid, "400.00")     # 75d -> d60
    i_ancient = _finalized_invoice(client, h, pid, "800.00")  # 200d -> d90p
    _backdate_invoice(session, i_fresh, 5)
    _backdate_invoice(session, i_mid, 45)
    _backdate_invoice(session, i_old, 75)
    _backdate_invoice(session, i_ancient, 200)

    row = _ageing(client, h)[0]
    assert row["lt30"] == "100.00"
    assert row["d30"] == "200.00"
    assert row["d60"] == "400.00"
    assert row["d90p"] == "800.00"
    assert row["total"] == "1500.00"
    assert row["worst_bucket"] == "d90p"
    assert row["is_overdue"] is True
    assert row["oldest_bill_number"] is not None
    assert row["open_invoice_count"] == 4


def test_boundary_29_is_lt30_and_30_is_d30(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "age2@x.example.com"))
    p_edge_lo = _party(client, h, "Edge 29 Co")
    p_edge_hi = _party(client, h, "Edge 30 Co")
    i_lo = _finalized_invoice(client, h, p_edge_lo, "111.00")
    i_hi = _finalized_invoice(client, h, p_edge_hi, "222.00")
    _backdate_invoice(session, i_lo, 29)
    _backdate_invoice(session, i_hi, 30)

    by_name = {r["legal_name"]: r for r in _ageing(client, h)}
    assert by_name["Edge 29 Co"]["worst_bucket"] == "lt30"
    assert by_name["Edge 29 Co"]["is_overdue"] is False
    assert by_name["Edge 30 Co"]["worst_bucket"] == "d30"
    assert by_name["Edge 30 Co"]["is_overdue"] is True


def test_credit_days_shift_the_due_date(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "age3@x.example.com"))
    pid = _party(client, h, "Credit Terms Co")
    inv = _finalized_invoice(client, h, pid, "500.00")
    _backdate_invoice(session, inv, 40)  # 40d old

    # credit_days 0 -> age 40 -> d30
    assert _ageing(client, h)[0]["worst_bucket"] == "d30"

    # credit_days 30 -> due date is 30d after invoice date -> age 10 -> lt30
    tenant = session.scalar(select(Tenant))
    tenant.default_credit_days = 30
    session.commit()
    assert _ageing(client, h)[0]["worst_bucket"] == "lt30"
    assert _ageing(client, h)[0]["is_overdue"] is False


def test_opening_balance_ages_by_as_of(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "age4@x.example.com"))
    # opening balance set on create, before any ledger history locks it
    r = client.post(
        "/api/parties",
        headers=h,
        json={
            "legal_name": "Carried Forward Co",
            "opening_balance": "9000.00",
            "opening_balance_as_of": str(date.today() - timedelta(days=120)),
        },
    )
    assert r.status_code == 201, r.text

    row = _ageing(client, h)[0]
    assert row["d90p"] == "9000.00"
    assert row["lt30"] == "0.00"
    assert row["worst_bucket"] == "d90p"
    assert row["total"] == "9000.00"
    assert row["open_invoice_count"] == 0


def test_opening_balance_null_as_of_falls_in_lt30(client: TestClient) -> None:
    h = _h(_register(client, "age5@x.example.com"))
    r = client.post(
        "/api/parties",
        headers=h,
        json={"legal_name": "No Date Co", "opening_balance": "1200.00"},
    )
    assert r.status_code == 201, r.text
    row = _ageing(client, h)[0]
    assert row["lt30"] == "1200.00"
    assert row["worst_bucket"] == "lt30"
    assert row["is_overdue"] is False


def test_paid_and_credit_parties_absent(client: TestClient) -> None:
    h = _h(_register(client, "age6@x.example.com"))
    p_paid = _party(client, h, "Settled Co")
    p_owes = _party(client, h, "Owes Co")
    i_paid = _finalized_invoice(client, h, p_paid, "300.00")
    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": p_paid,
            "amount": "300.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": i_paid, "type": "against_invoice", "amount": "300.00"}
            ],
        },
    )
    _finalized_invoice(client, h, p_owes, "700.00")

    names = {r["legal_name"] for r in _ageing(client, h)}
    assert names == {"Owes Co"}


def test_last_payment_date_and_none(client: TestClient) -> None:
    h = _h(_register(client, "age7@x.example.com"))
    pid = _party(client, h, "Part Payer Co")
    inv = _finalized_invoice(client, h, pid, "1000.00")
    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "400.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv, "type": "against_invoice", "amount": "400.00"}
            ],
        },
    )
    row = _ageing(client, h)[0]
    assert row["total"] == "600.00"  # live balance, not the original 1000
    assert row["last_payment_date"] == str(date.today())

    h2 = _h(_register(client, "age7b@x.example.com"))
    p2 = _party(client, h2, "Never Paid Co")
    _finalized_invoice(client, h2, p2, "500.00")
    assert _ageing(client, h2)[0]["last_payment_date"] is None


def test_search_filters_by_name(client: TestClient) -> None:
    h = _h(_register(client, "age8@x.example.com"))
    _finalized_invoice(client, h, _party(client, h, "Alpha Metals"), "100.00")
    _finalized_invoice(client, h, _party(client, h, "Beta Traders"), "100.00")
    rows = _ageing(client, h, q="alpha")
    assert [r["legal_name"] for r in rows] == ["Alpha Metals"]


def test_as_on_backdates_the_ageing(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "age9@x.example.com"))
    pid = _party(client, h, "Time Travel Co")
    inv = _finalized_invoice(client, h, pid, "250.00")
    _backdate_invoice(session, inv, 45)  # 45d old as of today -> d30

    assert _ageing(client, h)[0]["worst_bucket"] == "d30"
    # as_on 20 days ago -> the invoice was only 25d old then -> lt30
    as_on = str(date.today() - timedelta(days=20))
    assert _ageing(client, h, as_on=as_on)[0]["worst_bucket"] == "lt30"


def test_sort_worst_bucket_then_total(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "age10@x.example.com"))
    p_small_old = _party(client, h, "Small Old Co")
    p_big_fresh = _party(client, h, "Big Fresh Co")
    i_small = _finalized_invoice(client, h, p_small_old, "100.00")
    i_big = _finalized_invoice(client, h, p_big_fresh, "99999.00")
    _backdate_invoice(session, i_small, 200)  # d90p
    _backdate_invoice(session, i_big, 5)  # lt30

    rows = _ageing(client, h)
    # worst bucket wins over size — the tiny 200-day debt sorts first
    assert rows[0]["legal_name"] == "Small Old Co"
    assert rows[1]["legal_name"] == "Big Fresh Co"

"""Payments: create + allocate, balances, IDOR guards, reversal, collections,
voucher numbering.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _party(client: TestClient, h: dict, name: str = "Jay Matadee Enterprises") -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _finalized_invoice(client: TestClient, h: dict, pid: str, rate: str, qty: str = "1") -> dict:
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    r = client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "SS Utensil", "quantity": qty, "unit_rate": rate}]},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text
    return client.get(f"/api/invoices/{d['id']}", headers=h).json()


# --------------------------------------------------------------------------


def test_full_single_allocation_pays_invoice(client: TestClient) -> None:
    h = _h(_register(client, "p1@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "1000.00")
    total = inv["totals"]["grand_total"]

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": total,
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": total}
            ],
        },
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    assert pay["voucher_no"] == 1
    assert pay["status"] == "posted"
    assert len(pay["allocations"]) == 1

    got = client.get(f"/api/invoices/{inv['id']}", headers=h).json()
    assert got["balance_due"] == "0.00"
    assert got["payment_status"] == "paid"

    lst = client.get("/api/invoices", headers=h).json()
    row = next(x for x in lst if x["id"] == inv["id"])
    assert row["payment_status"] == "paid"


def test_split_across_two_invoices_partial_and_full(client: TestClient) -> None:
    h = _h(_register(client, "p2@x.example.com"))
    pid = _party(client, h)
    inv1 = _finalized_invoice(client, h, pid, "1000.00")
    inv2 = _finalized_invoice(client, h, pid, "500.00")

    # pay inv1 fully (1000) and inv2 partially (200 of 500)
    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "1200.00",
            "mode": "bank",
            "allocations": [
                {"invoice_id": inv1["id"], "type": "against_invoice", "amount": "1000.00"},
                {"invoice_id": inv2["id"], "type": "against_invoice", "amount": "200.00"},
            ],
        },
    )
    assert r.status_code == 201, r.text

    got1 = client.get(f"/api/invoices/{inv1['id']}", headers=h).json()
    assert got1["balance_due"] == "0.00"
    assert got1["payment_status"] == "paid"

    got2 = client.get(f"/api/invoices/{inv2['id']}", headers=h).json()
    assert got2["balance_due"] == "300.00"
    assert got2["payment_status"] == "partial"


def test_overpayment_becomes_on_account(client: TestClient) -> None:
    h = _h(_register(client, "p3@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "1000.00")

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "1500.00",
            "mode": "upi",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "1000.00"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    on_account = [a for a in pay["allocations"] if a["type"] == "on_account"]
    assert len(on_account) == 1
    assert on_account[0]["amount"] == "500.00"
    assert on_account[0]["invoice_id"] is None


def test_allocation_exceeding_live_balance_rejected(client: TestClient) -> None:
    h = _h(_register(client, "p4@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "1000.00")

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "2000.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "2000.00"}
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_allocation_against_other_party_invoice_rejected(client: TestClient) -> None:
    h = _h(_register(client, "p5@x.example.com"))
    pid1 = _party(client, h, "Party One")
    pid2 = _party(client, h, "Party Two")
    inv = _finalized_invoice(client, h, pid1, "1000.00")

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid2,
            "amount": "500.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "500.00"}
            ],
        },
    )
    assert r.status_code == 404, r.text


def test_allocation_against_other_tenant_invoice_rejected(client: TestClient) -> None:
    h1 = _h(_register(client, "p6a@x.example.com"))
    h2 = _h(_register(client, "p6b@x.example.com"))
    pid1 = _party(client, h1)
    inv = _finalized_invoice(client, h1, pid1, "1000.00")

    pid2 = _party(client, h2, "Tenant2 Party")
    r = client.post(
        "/api/payments",
        headers=h2,
        json={
            "party_id": pid2,
            "amount": "500.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "500.00"}
            ],
        },
    )
    assert r.status_code == 404, r.text


def test_allocation_against_draft_invoice_rejected(client: TestClient) -> None:
    h = _h(_register(client, "p7@x.example.com"))
    pid = _party(client, h)
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "100.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": d["id"], "type": "against_invoice", "amount": "100.00"}
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_allocation_against_cancelled_invoice_rejected(client: TestClient) -> None:
    h = _h(_register(client, "p8@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "1000.00")
    r = client.post(f"/api/invoices/{inv['id']}/cancel", headers=h)
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "100.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "100.00"}
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_reverse_payment_restores_balance(client: TestClient) -> None:
    h = _h(_register(client, "p9@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "1000.00")

    pay = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "1000.00",
            "mode": "cheque",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "1000.00"}
            ],
        },
    ).json()

    got = client.get(f"/api/invoices/{inv['id']}", headers=h).json()
    assert got["payment_status"] == "paid"

    r = client.post(
        f"/api/payments/{pay['id']}/reverse", headers=h, json={"reason": "cheque bounced"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reversed"
    assert r.json()["reversed_reason"] == "cheque bounced"

    got2 = client.get(f"/api/invoices/{inv['id']}", headers=h).json()
    assert got2["payment_status"] == "unpaid"
    assert got2["balance_due"] == "1000.00"


def test_reverse_already_reversed_conflicts(client: TestClient) -> None:
    h = _h(_register(client, "p10@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "500.00")

    pay = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "500.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "500.00"}
            ],
        },
    ).json()
    r1 = client.post(f"/api/payments/{pay['id']}/reverse", headers=h, json={"reason": "x"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/payments/{pay['id']}/reverse", headers=h, json={"reason": "y"})
    assert r2.status_code == 409


def test_collections_list_balance_and_oldest_sort(client: TestClient) -> None:
    h = _h(_register(client, "p11@x.example.com"))
    p_paid = _party(client, h, "Fully Paid Co")
    p_owes_small = _party(client, h, "Small Debt Co")
    p_owes_big = _party(client, h, "Big Debt Co")

    inv_paid = _finalized_invoice(client, h, p_paid, "100.00")
    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": p_paid,
            "amount": "100.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv_paid["id"], "type": "against_invoice", "amount": "100.00"}
            ],
        },
    )
    _finalized_invoice(client, h, p_owes_small, "50.00")
    _finalized_invoice(client, h, p_owes_big, "5000.00")

    r = client.get("/api/collections", headers=h, params={"sort": "balance"})
    assert r.status_code == 200, r.text
    rows = r.json()
    names = [row["legal_name"] for row in rows]
    assert "Fully Paid Co" not in names  # balance 0 excluded
    assert names[0] == "Big Debt Co"  # highest balance first
    assert "Small Debt Co" in names

    r2 = client.get("/api/collections", headers=h, params={"sort": "oldest"})
    assert r2.status_code == 200
    assert {row["legal_name"] for row in r2.json()} == {"Big Debt Co", "Small Debt Co"}


def test_collections_overpaid_scope(client: TestClient) -> None:
    """A party who has fully paid their only invoice AND left an on-account
    credit has NO open invoice at all — the old INNER-JOIN-to-open-invoices
    query structurally couldn't surface that party under any scope. The
    party-driven LEFT JOIN must.
    """
    h = _h(_register(client, "p13@x.example.com"))
    p_credit = _party(client, h, "Overpaid Co")
    p_owes = _party(client, h, "Owes Co")

    inv = _finalized_invoice(client, h, p_credit, "100.00")
    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": p_credit,
            "amount": "150.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "100.00"}
            ],
        },
    )
    _finalized_invoice(client, h, p_owes, "75.00")

    r = client.get("/api/collections", headers=h, params={"scope": "outstanding"})
    assert r.status_code == 200, r.text
    names = {row["legal_name"] for row in r.json()}
    assert names == {"Owes Co"}  # the overpaid party must NOT show here

    r2 = client.get("/api/collections", headers=h, params={"scope": "overpaid"})
    assert r2.status_code == 200, r2.text
    rows2 = r2.json()
    assert {row["legal_name"] for row in rows2} == {"Overpaid Co"}
    row = rows2[0]
    assert row["open_invoice_count"] == 0
    assert row["oldest_unpaid_days"] is None
    # positive in the payload — the frontend takes abs() and labels it a credit
    assert float(row["outstanding_balance"]) == pytest.approx(-50.00)

    r3 = client.get("/api/collections", headers=h, params={"scope": "either"})
    assert r3.status_code == 200, r3.text
    assert {row["legal_name"] for row in r3.json()} == {"Owes Co", "Overpaid Co"}


def test_voucher_no_gap_free_and_sequential(client: TestClient) -> None:
    h = _h(_register(client, "p12@x.example.com"))
    pid = _party(client, h)
    inv1 = _finalized_invoice(client, h, pid, "100.00")
    inv2 = _finalized_invoice(client, h, pid, "100.00")

    pay1 = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "100.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv1["id"], "type": "against_invoice", "amount": "100.00"}
            ],
        },
    ).json()
    pay2 = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "100.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv2["id"], "type": "against_invoice", "amount": "100.00"}
            ],
        },
    ).json()
    assert pay1["voucher_no"] == 1
    assert pay2["voucher_no"] == 2


def test_open_invoices_and_ledger_endpoints(client: TestClient) -> None:
    h = _h(_register(client, "p13@x.example.com"))
    pid = _party(client, h)
    inv1 = _finalized_invoice(client, h, pid, "1000.00")
    inv2 = _finalized_invoice(client, h, pid, "500.00")

    r = client.get(f"/api/parties/{pid}/open-invoices", headers=h)
    assert r.status_code == 200, r.text
    open_ids = {row["invoice_id"] for row in r.json()}
    assert open_ids == {inv1["id"], inv2["id"]}

    client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "1000.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv1["id"], "type": "against_invoice", "amount": "1000.00"}
            ],
        },
    )

    r2 = client.get(f"/api/parties/{pid}/open-invoices", headers=h)
    open_ids2 = {row["invoice_id"] for row in r2.json()}
    assert open_ids2 == {inv2["id"]}

    r3 = client.get(f"/api/parties/{pid}/ledger", headers=h)
    assert r3.status_code == 200, r3.text
    kinds = [e["kind"] for e in r3.json()]
    assert kinds.count("invoice") == 2
    assert kinds.count("payment") == 1

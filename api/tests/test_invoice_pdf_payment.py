"""PDF context: paid_amount / balance_due must reflect payments recorded
either before finalize (the editor's "Partial"/"Paid in full" finalize-time
option) or any time after (Collections / Account tab / balance strip), and
must never leak onto a draft or a fully-unpaid bill.

WeasyPrint's native libs aren't available on a bare dev box (see
`services/invoices/pdf.py`'s module docstring), so these tests exercise the
same context-building + Jinja render path `render_invoice_pdf` uses, minus
the final HTML->PDF step, by calling the template directly with the same
inputs `render_invoice_pdf` would compute.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import Invoice
from app.models._mixins import InvoiceStatus
from app.services.invoices.pdf import _env
from app.services.payments import balance_due_for_invoice, paid_amount_for_invoice


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


def _pdf_context(session: Session, invoice: Invoice) -> tuple[Decimal | None, Decimal | None]:
    """Mirrors render_invoice_pdf's paid/balance gating exactly."""
    if invoice.status != InvoiceStatus.final:
        return None, None
    return (
        paid_amount_for_invoice(session, invoice.id),
        balance_due_for_invoice(session, invoice),
    )


def _render_totals_block(paid_amount, balance_due) -> str:
    tmpl = _env.from_string(
        """
        {% if paid_amount is not none and paid_amount|float > 0 %}
        Amount Received: {{ paid_amount|money }}
        Balance Due: {{ balance_due|money }}
        {% endif %}
        """
    )
    return tmpl.render(paid_amount=paid_amount, balance_due=balance_due)


def test_partial_payment_before_finalize_shows_on_pdf(client: TestClient, session: Session) -> None:
    """The editor's finalize-time 'Partial' payment option records a payment
    immediately after finalize succeeds — the very next PDF render (or any
    later re-render) must show it, not just a bare Grand Total.
    """
    h = _h(_register(client, "pdf1@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "1000.00")

    # same call shape the editor's "Partial" finalize-time flow makes
    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "400.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "400.00"}
            ],
        },
    )
    assert r.status_code == 201, r.text

    invoice = session.get(Invoice, inv["id"])
    assert invoice is not None
    paid, balance = _pdf_context(session, invoice)
    assert paid == Decimal("400.00")
    assert balance == Decimal("600.00")

    block = _render_totals_block(paid, balance)
    assert "Amount Received: 400.00" in block
    assert "Balance Due: 600.00" in block


def test_payment_recorded_after_finalize_also_shows(client: TestClient, session: Session) -> None:
    """A payment recorded well after finalize (Collections / Account tab /
    invoice balance strip — not at finalize time) must show on a re-render
    the same way."""
    h = _h(_register(client, "pdf2@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "500.00")

    invoice = session.get(Invoice, inv["id"])
    assert invoice is not None
    paid0, balance0 = _pdf_context(session, invoice)
    assert paid0 == Decimal("0.00")
    assert balance0 == Decimal("500.00")
    # zero paid -> the block is omitted entirely, not printed as "0.00"
    assert _render_totals_block(paid0, balance0).strip() == ""

    r = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "500.00",
            "mode": "upi",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "500.00"}
            ],
        },
    )
    assert r.status_code == 201, r.text

    session.expire_all()
    invoice = session.get(Invoice, inv["id"])
    assert invoice is not None
    paid1, balance1 = _pdf_context(session, invoice)
    assert paid1 == Decimal("500.00")
    assert balance1 == Decimal("0.00")
    block = _render_totals_block(paid1, balance1)
    assert "Amount Received: 500.00" in block
    assert "Balance Due: 0.00" in block


def test_draft_invoice_has_no_payment_context(client: TestClient, session: Session) -> None:
    """A draft (never finalized) has no frozen grand_total to bill against —
    paid/balance must be None, not a computed (and meaningless) figure."""
    h = _h(_register(client, "pdf3@x.example.com"))
    pid = _party(client, h)
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()

    invoice = session.get(Invoice, d["id"])
    assert invoice is not None
    assert invoice.status == InvoiceStatus.draft
    paid, balance = _pdf_context(session, invoice)
    assert paid is None
    assert balance is None
    assert _render_totals_block(paid, balance).strip() == ""


def test_reversed_payment_does_not_count_on_pdf(client: TestClient, session: Session) -> None:
    """A reversed payment's allocations must stop counting immediately —
    printing a reversed payment as if it were still received would be a
    real accounting error on the customer-facing document."""
    h = _h(_register(client, "pdf4@x.example.com"))
    pid = _party(client, h)
    inv = _finalized_invoice(client, h, pid, "300.00")

    pay = client.post(
        "/api/payments",
        headers=h,
        json={
            "party_id": pid,
            "amount": "300.00",
            "mode": "cash",
            "allocations": [
                {"invoice_id": inv["id"], "type": "against_invoice", "amount": "300.00"}
            ],
        },
    ).json()

    r = client.post(
        f"/api/payments/{pay['id']}/reverse", headers=h, json={"reason": "wrong entry"}
    )
    assert r.status_code == 200, r.text

    session.expire_all()
    invoice = session.get(Invoice, inv["id"])
    assert invoice is not None
    paid, balance = _pdf_context(session, invoice)
    assert paid == Decimal("0.00")
    assert balance == Decimal("300.00")
    assert _render_totals_block(paid, balance).strip() == ""

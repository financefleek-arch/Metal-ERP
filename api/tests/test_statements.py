"""F3b — party account statement: period resolver, windowed ledger data
(carried-forward opening balance, reversed payments kept struck-through,
empty-window flag), and the WhatsApp send path.

The PDF render (WeasyPrint native libs) is patched out — these tests cover
`resolve_period` and `party_statement_data` directly, and `send_party_statement`
with the Meta HTTP + PDF calls stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import Invoice
from app.services import whatsapp as wa
from app.services.statements import (
    StatementError,
    party_statement_data,
    resolve_period,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _register(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Statement Test Co", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _party(client: TestClient, h: dict, **extra: object) -> str:
    r = client.post("/api/parties", headers=h, json={"legal_name": "Ledger Party", **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _final_invoice(client: TestClient, h: dict, pid: str, rate: str) -> str:
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "Item", "quantity": "1", "unit_rate": rate}]},
    )
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text
    return d["id"]


def _pay(client: TestClient, h: dict, pid: str, amount: str, invoice_id: str | None = None) -> str:
    allocs = (
        [{"invoice_id": invoice_id, "type": "against_invoice", "amount": amount}]
        if invoice_id
        else []
    )
    r = client.post(
        "/api/payments",
        headers=h,
        json={"party_id": pid, "amount": amount, "mode": "cash", "allocations": allocs},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_date(session: Session, model, obj_id: str, d: date) -> None:
    obj = session.scalar(select(model).where(model.id == obj_id))
    obj.date = d
    session.commit()


# --------------------------------------------------------------------------
# resolve_period
# --------------------------------------------------------------------------


def test_period_presets() -> None:
    t = date(2026, 9, 9)
    assert resolve_period("this_month", today=t) == (date(2026, 9, 1), t)
    assert resolve_period("last_month", today=t) == (date(2026, 8, 1), date(2026, 8, 31))
    assert resolve_period("this_fy", today=t) == (date(2026, 4, 1), t)
    # FY is Apr-Mar, hard-wired — a Feb date is in the FY that started last April
    assert resolve_period("this_fy", today=date(2026, 2, 15)) == (
        date(2025, 4, 1),
        date(2026, 2, 15),
    )
    assert resolve_period("last_90", today=t) == (date(2026, 6, 12), t)


def test_custom_period_requires_both_dates() -> None:
    with pytest.raises(StatementError, match="requires from and to"):
        resolve_period("custom", dt_from=date(2026, 1, 1))
    with pytest.raises(StatementError, match="after"):
        resolve_period("custom", dt_from=date(2026, 5, 1), dt_to=date(2026, 4, 1))
    assert resolve_period(
        "custom", dt_from=date(2026, 3, 1), dt_to=date(2026, 3, 31)
    ) == (date(2026, 3, 1), date(2026, 3, 31))


def test_unknown_period() -> None:
    with pytest.raises(StatementError, match="unknown period"):
        resolve_period("yesterday")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# party_statement_data
# --------------------------------------------------------------------------


def test_opening_balance_carries_pre_window_forward(
    client: TestClient, session: Session
) -> None:
    h = _h(_register(client, "st1@x.example.com"))
    pid = _party(client, h)
    i_old = _final_invoice(client, h, pid, "1000.00")  # before window
    i_in = _final_invoice(client, h, pid, "400.00")  # in window
    _set_date(session, Invoice, i_old, date.today() - timedelta(days=60))
    _set_date(session, Invoice, i_in, date.today() - timedelta(days=5))

    start = date.today() - timedelta(days=30)
    data = party_statement_data(session, pid, period_from=start, period_to=date.today())

    # the 1000 pre-window invoice is folded into the opening figure, not listed
    assert data.opening_balance == pytest.approx(1000.00)
    assert len(data.rows) == 1
    assert data.rows[0].particulars.startswith("INV-")
    assert data.rows[0].debit == pytest.approx(400.00)
    assert data.rows[0].running_balance == pytest.approx(1400.00)
    assert data.total_due == pytest.approx(1400.00)
    assert data.is_empty is False


def test_reversed_payment_kept_struck_through_nil_effect(
    client: TestClient, session: Session
) -> None:
    h = _h(_register(client, "st2@x.example.com"))
    pid = _party(client, h)
    inv = _final_invoice(client, h, pid, "5000.00")
    _set_date(session, Invoice, inv, date.today() - timedelta(days=3))
    pay = _pay(client, h, pid, "2000.00", inv)
    r = client.post(f"/api/payments/{pay}/reverse", headers=h, json={"reason": "cheque bounced"})
    assert r.status_code == 200

    data = party_statement_data(
        session, pid, period_from=date.today() - timedelta(days=30), period_to=date.today()
    )
    rev_rows = [row for row in data.rows if row.reversed]
    assert len(rev_rows) == 1
    assert "reversed (cheque bounced)" in rev_rows[0].particulars
    # balance is unchanged by the reversed payment — still the full 5000
    assert data.total_due == pytest.approx(5000.00)
    assert rev_rows[0].running_balance == pytest.approx(5000.00)


def test_empty_window_flag(client: TestClient, session: Session) -> None:
    h = _h(_register(client, "st3@x.example.com"))
    pid = _party(client, h)
    inv = _final_invoice(client, h, pid, "700.00")
    _set_date(session, Invoice, inv, date.today() - timedelta(days=120))  # well before window

    data = party_statement_data(
        session, pid, period_from=date.today() - timedelta(days=30), period_to=date.today()
    )
    assert data.is_empty is True
    assert data.rows == []
    assert data.opening_balance == pytest.approx(700.00)
    assert data.total_due == pytest.approx(700.00)


def test_pdf_route_422_on_bad_custom_range(client: TestClient) -> None:
    h = _h(_register(client, "st4@x.example.com"))
    pid = _party(client, h)
    r = client.get(
        f"/api/parties/{pid}/statement.pdf",
        headers=h,
        params={"period": "custom", "from": "2026-05-01"},  # no `to`
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------
# send_party_statement — Meta HTTP + PDF render stubbed
# --------------------------------------------------------------------------


@pytest.fixture
def _stub_statement_send(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    monkeypatch.setattr(wa, "get_config", lambda *a, **k: object())
    monkeypatch.setattr(wa, "upload_media", lambda *a, **k: "media.TEST")

    def _fake_render(session, party_id, *, period_from, period_to):
        from app.services.statements import party_statement_data

        data = party_statement_data(
            session, party_id, period_from=period_from, period_to=period_to
        )
        import pathlib

        return pathlib.Path("/tmp/does-not-need-to-exist.pdf"), data

    monkeypatch.setattr(wa, "render_party_statement_pdf", None, raising=False)
    # send_party_statement imports render_party_statement_pdf locally from
    # app.services.statements — patch it there.
    import app.services.statements as st

    monkeypatch.setattr(st, "render_party_statement_pdf", _fake_render)

    def _fake_send(cfg, *, to_phone, template_name, body_params, **_):
        seen["to_phone"] = to_phone
        seen["template_name"] = template_name
        seen["body_params"] = body_params
        return "wamid.STMT"

    monkeypatch.setattr(wa, "_send_template_message", _fake_send)
    return seen


def test_send_uses_party_phone_and_account_statement_template(
    client: TestClient, session: Session, _stub_statement_send
) -> None:
    h = _h(_register(client, "st5@x.example.com"))
    pid = _party(client, h, phone="98320 11223")
    inv = _final_invoice(client, h, pid, "1500.00")
    _set_date(session, Invoice, inv, date.today() - timedelta(days=2))

    msg = wa.send_party_statement(session, pid, period="this_month")
    assert msg.status == "sent"
    assert msg.template_name == "account_statement"
    assert msg.party_id == pid
    assert msg.invoice_id is None
    assert _stub_statement_send["to_phone"] == "919832011223"
    assert _stub_statement_send["template_name"] == "account_statement"
    # body params: (party_name, total_due) — bare numeric string
    assert _stub_statement_send["body_params"] == ["Ledger Party", "1500.00"]


def test_send_explicit_phone_bypasses_party(
    client: TestClient, session: Session, _stub_statement_send
) -> None:
    h = _h(_register(client, "st6@x.example.com"))
    pid = _party(client, h)  # no phone on file
    inv = _final_invoice(client, h, pid, "800.00")
    _set_date(session, Invoice, inv, date.today() - timedelta(days=1))

    msg = wa.send_party_statement(
        session, pid, period="this_month", to_phone="+919812345678"
    )
    assert msg.status == "sent"
    assert _stub_statement_send["to_phone"] == "919812345678"


def test_send_empty_window_is_rejected(
    client: TestClient, session: Session, _stub_statement_send
) -> None:
    h = _h(_register(client, "st7@x.example.com"))
    pid = _party(client, h, phone="98320 11223")
    inv = _final_invoice(client, h, pid, "900.00")
    _set_date(session, Invoice, inv, date.today() - timedelta(days=120))  # before this month

    with pytest.raises(wa.WhatsappError, match="[Nn]othing happened"):
        wa.send_party_statement(session, pid, period="this_month")


def test_send_party_without_phone_rejected(
    client: TestClient, session: Session, _stub_statement_send
) -> None:
    h = _h(_register(client, "st8@x.example.com"))
    pid = _party(client, h)  # no phone
    inv = _final_invoice(client, h, pid, "300.00")
    _set_date(session, Invoice, inv, date.today() - timedelta(days=2))

    with pytest.raises(wa.WhatsappError, match="no phone"):
        wa.send_party_statement(session, pid, period="this_month")

"""`send_invoice` recipient resolution + `handle_status_webhook` fan-in.

send_invoice: sending to the invoice's party needs only a phone on file —
there is no opt-in flag to set (dropped 2026-09-08). An explicit `to_phone`
bypasses the party entirely. The Meta HTTP calls (`get_config`, media
upload, template send) are patched out.

handle_status_webhook: fleek-backend fans out the whole shared-app webhook
firehose to us, so a status for an unknown wamid must be ignored, and a
`failed` status must record a short readable error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Invoice
from app.models.whatsapp import WhatsappMessage
from app.services import whatsapp as wa


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


def _party(client: TestClient, h: dict, **extra: object) -> str:
    r = client.post(
        "/api/parties",
        headers=h,
        json={"legal_name": "Jay Matadee Enterprises", **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _final_invoice(client: TestClient, h: dict, pid: str) -> str:
    d = client.post("/api/invoices", headers=h, json={"party_id": pid}).json()
    client.put(
        f"/api/invoices/{d['id']}",
        headers=h,
        json={"lines": [{"description": "SS Utensil", "quantity": "5", "unit_rate": "100"}]},
    )
    r = client.post(f"/api/invoices/{d['id']}/finalize", headers=h)
    assert r.status_code == 200, r.text
    return d["id"]


@pytest.fixture
def _stub_send(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the recipient `send_invoice` hands to Meta; skip the network."""
    seen: list[str] = []

    monkeypatch.setattr(wa, "get_config", lambda *a, **k: object())
    monkeypatch.setattr(wa, "upload_media", lambda *a, **k: None)

    def _fake_send(cfg: object, *, to_phone: str, **_: object) -> str:
        seen.append(to_phone)
        return "wamid.TESTID"

    monkeypatch.setattr(wa, "_send_template_message", _fake_send)
    return seen


def test_send_to_party_needs_only_a_phone(client: TestClient, session, _stub_send) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-optin@x.example.com"))
    pid = _party(client, h, phone="98320 11223")  # no opt-in flag anywhere
    iid = _final_invoice(client, h, pid)

    inv = session.scalar(select(Invoice).where(Invoice.id == iid))
    msg = wa.send_invoice(session, inv, template_name="invoice_ready")

    assert msg.status == "sent"
    assert _stub_send == ["919832011223"]


def test_send_to_party_without_phone_is_rejected(client: TestClient, session, _stub_send) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-nophone@x.example.com"))
    pid = _party(client, h)  # no phone
    iid = _final_invoice(client, h, pid)

    inv = session.scalar(select(Invoice).where(Invoice.id == iid))
    with pytest.raises(wa.WhatsappError, match="no phone"):
        wa.send_invoice(session, inv, template_name="invoice_ready")
    assert _stub_send == []


def test_explicit_to_phone_bypasses_party(client: TestClient, session, _stub_send) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-explicit@x.example.com"))
    pid = _party(client, h)  # party has no phone
    iid = _final_invoice(client, h, pid)

    inv = session.scalar(select(Invoice).where(Invoice.id == iid))
    msg = wa.send_invoice(
        session, inv, template_name="invoice_ready", to_phone="+919812345678"
    )
    assert msg.status == "sent"
    assert _stub_send == ["919812345678"]


def _statuses_payload(wamid: str, status: str, errors: list | None = None) -> dict:
    st: dict = {"id": wamid, "status": status, "timestamp": "1725800000"}
    if errors is not None:
        st["errors"] = errors
    return {"entry": [{"changes": [{"value": {"statuses": [st]}}]}]}


def test_status_webhook_advances_matching_message(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-wh1@x.example.com"))
    pid = _party(client, h, phone="98765 43210")
    iid = _final_invoice(client, h, pid)
    inv = session.scalar(select(Invoice).where(Invoice.id == iid))

    msg = WhatsappMessage(
        tenant_id=inv.tenant_id,
        invoice_id=inv.id,
        template_name="invoice_ready",
        to_phone="919876543210",
        wa_message_id="wamid.MINE",
        status="sent",
    )
    session.add(msg)
    session.flush()

    wa.handle_status_webhook(session, _statuses_payload("wamid.MINE", "delivered"))
    session.refresh(msg)
    assert msg.status == "delivered" and msg.delivered_at is not None

    wa.handle_status_webhook(session, _statuses_payload("wamid.MINE", "read"))
    session.refresh(msg)
    assert msg.status == "read" and msg.read_at is not None


def test_status_webhook_ignores_unknown_wamid(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    # No row for this id — a fleek-backend message fanned out to us. Must be
    # a silent no-op, not an error.
    wa.handle_status_webhook(
        session, _statuses_payload("wamid.SOMEONE_ELSES", "delivered")
    )


def test_status_webhook_failed_records_readable_error(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-wh2@x.example.com"))
    pid = _party(client, h, phone="98765 43210")
    iid = _final_invoice(client, h, pid)
    inv = session.scalar(select(Invoice).where(Invoice.id == iid))

    msg = WhatsappMessage(
        tenant_id=inv.tenant_id,
        invoice_id=inv.id,
        template_name="invoice_ready",
        to_phone="919876543210",
        wa_message_id="wamid.FAIL",
        status="sent",
    )
    session.add(msg)
    session.flush()

    wa.handle_status_webhook(
        session,
        _statuses_payload(
            "wamid.FAIL",
            "failed",
            errors=[
                {
                    "code": 131026,
                    "title": "Message undeliverable",
                    "error_data": {"details": "Receiver is not a valid WhatsApp user"},
                }
            ],
        ),
    )
    session.refresh(msg)
    assert msg.status == "failed"
    assert "Message undeliverable" in msg.error
    assert "131026" in msg.error
    assert "valid WhatsApp user" in msg.error
    assert "{" not in msg.error  # not a raw dict dump


# --------------------------------------------------------------------------
# per-invoice message log + list column
# --------------------------------------------------------------------------


def _add_msg(session, inv, *, wamid: str, status: str, created=None) -> WhatsappMessage:  # type: ignore[no-untyped-def]
    m = WhatsappMessage(
        tenant_id=inv.tenant_id,
        invoice_id=inv.id,
        template_name="invoice_ready",
        to_phone="919876543210",
        wa_message_id=wamid,
        status=status,
    )
    session.add(m)
    session.flush()
    if created is not None:  # rows created microseconds apart share a timestamp
        m.created_at = created
        session.flush()
    return m


def test_invoice_whatsapp_log_endpoint(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-log@x.example.com"))
    pid = _party(client, h, phone="98765 43210")
    iid = _final_invoice(client, h, pid)
    inv = session.scalar(select(Invoice).where(Invoice.id == iid))

    t0 = datetime.now(UTC)
    _add_msg(session, inv, wamid="wamid.A", status="sent", created=t0)
    _add_msg(
        session, inv, wamid="wamid.B", status="failed", created=t0 + timedelta(minutes=5)
    )
    session.commit()

    r = client.get(f"/api/invoices/{iid}/whatsapp", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["wa_message_id"] for x in rows] == ["wamid.B", "wamid.A"]  # newest first
    assert {x["status"] for x in rows} == {"sent", "failed"}

    # empty for an invoice that was never sent
    iid2 = _final_invoice(client, h, pid)
    r2 = client.get(f"/api/invoices/{iid2}/whatsapp", headers=h)
    assert r2.status_code == 200 and r2.json() == []


def test_invoice_whatsapp_log_is_tenant_scoped(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h1 = _h(_register(client, "wa-t1@x.example.com"))
    pid = _party(client, h1, phone="98765 43210")
    iid = _final_invoice(client, h1, pid)

    h2 = _h(_register(client, "wa-t2@x.example.com"))
    assert client.get(f"/api/invoices/{iid}/whatsapp", headers=h2).status_code == 404


def test_invoice_list_carries_latest_whatsapp_status(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    h = _h(_register(client, "wa-listcol@x.example.com"))
    pid = _party(client, h, phone="98765 43210")
    iid = _final_invoice(client, h, pid)
    inv = session.scalar(select(Invoice).where(Invoice.id == iid))

    # never sent -> null
    row = next(x for x in client.get("/api/invoices", headers=h).json() if x["id"] == iid)
    assert row["whatsapp_status"] is None

    t0 = datetime.now(UTC)
    _add_msg(session, inv, wamid="wamid.OLD", status="sent", created=t0)
    _add_msg(
        session,
        inv,
        wamid="wamid.NEW",
        status="delivered",
        created=t0 + timedelta(minutes=5),
    )
    session.commit()

    row = next(x for x in client.get("/api/invoices", headers=h).json() if x["id"] == iid)
    assert row["whatsapp_status"] == "delivered"  # latest row wins

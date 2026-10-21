"""A linked firm's WhatsApp messages go through Fleek; an unlinked firm keeps the direct route.

Fleek is replaced by a fake, and the direct Meta calls are made to fail loudly so a message that
should have gone through Fleek can never reach Meta by accident.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.models import Tenant
from app.models.whatsapp import WhatsappMessage
from app.services import fleek_messages
from app.services import whatsapp as wa
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, check_signature

SECRET = "test-control-plane-secret"
ORG = "org-shop-1"


@pytest.fixture(autouse=True)
def _cp(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "control_plane_secret", SECRET)
    monkeypatch.setattr(s, "control_plane_url", "https://fleek.test")


@pytest.fixture(autouse=True)
def _no_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("a linked firm's message must not call Meta directly")

    monkeypatch.setattr(wa, "_send_template_message", boom)
    monkeypatch.setattr(wa, "upload_media", boom)
    monkeypatch.setattr(wa, "_upload_media_bytes", boom)


class FakeFleek:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answer: tuple[int, dict[str, Any]] = (
            200,
            {"status": "sent", "provider_message_id": "wamid.FLEEK"},
        )
        self.down = False

        def send(org: str, payload: dict[str, Any], **_: object) -> tuple[int, dict[str, Any]]:
            if self.down:
                raise fleek_messages.FleekUnavailable("Could not reach Fleek: ConnectError")
            self.calls.append((org, payload))
            return self.answer

        monkeypatch.setattr(fleek_messages, "send", send)


@pytest.fixture
def fleek(monkeypatch: pytest.MonkeyPatch) -> FakeFleek:
    return FakeFleek(monkeypatch)


def _firm(session, linked: bool = True) -> Tenant:  # type: ignore[no-untyped-def]
    t = Tenant(legal_name="Sethia Metal Store", fleek_org_id=ORG if linked else None)
    session.add(t)
    session.flush()
    return t


def _order_message(session, tenant: Tenant, **over: Any) -> WhatsappMessage:  # type: ignore[no-untyped-def]
    args: dict[str, Any] = {
        "tenant_id": tenant.id,
        "order_id": None,
        "event": "placed",
        "template_name": "order_received",
        "to_phone": "98320 11223",
        "values": {
            "customer_name": "Ramesh",
            "shop_name": "Sethia\nMetal   Store",
            "order_number": "ORD-0042",
            "item_count": "2",
            "total": "3082.00",
        },
        "button_url_param": "Ab12Cd34Ef56",
    }
    args.update(over)
    return wa.send_order_message(session, **args)


# ------------------------------------------------------------------------------ the signed call
def test_the_call_is_signed_like_every_control_plane_call() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "sent", "provider_message_id": "wamid.X"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    status, body = fleek_messages.send(
        ORG, {"template_key": "invoice_ready", "to": "919832011223"}, client=client
    )
    assert (status, body["provider_message_id"]) == (200, "wamid.X")
    req = seen[0]
    assert req.url == f"https://fleek.test/api/internal/metal-erp/orgs/{ORG}/messages"
    check_signature(  # raises unless Fleek would accept it
        SECRET,
        timestamp=req.headers[TIMESTAMP_HEADER],
        signature=req.headers[SIGNATURE_HEADER],
        method="POST",
        path=req.url.path,
        body=req.content,
    )


def test_fleeks_refusal_comes_back_unchanged_and_an_unreachable_fleek_raises() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(409, json={"error": "not_approved"}))
    )
    assert fleek_messages.send(ORG, {}, client=client) == (409, {"error": "not_approved"})

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(fleek_messages.FleekUnavailable):
        fleek_messages.send(ORG, {}, client=httpx.Client(transport=httpx.MockTransport(down)))


# ------------------------------------------------------------------------------ choosing the route
def test_a_linked_firm_goes_through_fleek_and_an_unlinked_one_stays_direct(session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert wa.route_for(session, _firm(session).id).fleek_org_id == ORG
    unlinked = _firm(session, linked=False)
    with pytest.raises(wa.WhatsappNotConfigured):  # no token, no number: as before it was linked
        wa.route_for(session, unlinked.id)


def test_a_linked_firm_with_no_control_plane_setting_falls_back_to_the_direct_route(
    session, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "control_plane_url", None)
    with pytest.raises(wa.WhatsappNotConfigured):
        wa.route_for(session, _firm(session).id)


# ------------------------------------------------------------------------------ what Fleek is asked
def test_an_order_message_names_its_values_and_carries_the_order_link(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    msg = _order_message(session, _firm(session))
    assert (msg.status, msg.wa_message_id) == ("sent", "wamid.FLEEK")
    org, payload = fleek.calls[0]
    assert org == ORG
    assert payload["template_key"] == "order_received" and payload["to"] == "919832011223"
    assert payload["values"]["shop_name"] == "Sethia Metal Store"  # newline and extra spaces removed
    assert payload["links"] == {"order": "Ab12Cd34Ef56"} and payload["request_id"] == msg.id
    assert "document" not in payload


def test_a_pdf_is_sent_to_fleek_as_base64_with_its_file_name(
    session, fleek: FakeFleek, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 invoice")
    route = wa.route_for(session, _firm(session).id)
    wa_id, media_id = wa._deliver(
        route,
        request_id="m1",
        template_name="invoice_ready",
        recipient="919832011223",
        values={"party_name": "Ramesh", "invoice_number": "1224", "grand_total": "3082.00"},
        document_path=str(pdf),
        document_filename="Invoice-1224.pdf",
    )
    assert (wa_id, media_id) == ("wamid.FLEEK", None)
    doc = fleek.calls[0][1]["document"]
    assert (
        doc["filename"] == "Invoice-1224.pdf"
        and base64.b64decode(doc["content_base64"]) == b"%PDF-1.4 invoice"
    )


def test_a_missing_pdf_file_is_simply_not_attached(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    route = wa.route_for(session, _firm(session).id)
    wa._deliver(
        route,
        request_id="m1",
        template_name="invoice_ready",
        recipient="919832011223",
        values={"party_name": "R", "invoice_number": "1", "grand_total": "1.00"},
        document_path="/nowhere/inv.pdf",
        document_filename="x.pdf",
    )
    assert "document" not in fleek.calls[0][1]


def test_the_test_send_attaches_its_sample_pdf_through_fleek(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    t = _firm(session)
    msg = wa.send_test_message(session, t.id, to_phone="98320 11223")
    assert msg.status == "sent"
    doc = fleek.calls[0][1]["document"]
    assert base64.b64decode(doc["content_base64"]).startswith(b"%PDF")


# ------------------------------------------------------------------------------ when it does not go
def test_fleeks_reason_for_a_refusal_is_kept_on_the_failed_message(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    fleek.answer = (
        409,
        {
            "error": "not_approved",
            "message": "That message is not approved on this shop's WhatsApp account yet.",
        },
    )
    t = _firm(session)
    with pytest.raises(wa.WhatsappError, match="not approved"):
        _order_message(session, t)
    row = session.query(WhatsappMessage).filter_by(tenant_id=t.id).one()
    assert row.status == "failed" and "not approved" in (row.error or "")


def test_a_shop_with_no_registered_number_is_reported_as_not_configured(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    fleek.answer = (
        409,
        {"error": "not_connected", "message": "This shop has no registered WhatsApp number."},
    )
    with pytest.raises(wa.WhatsappNotConfigured):
        _order_message(session, _firm(session))


def test_a_meta_refusal_through_fleek_is_a_failed_message(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    fleek.answer = (
        502,
        {"error": "send_failed", "message": "This number is not on WhatsApp.", "failure": {"code": 131026}},
    )
    t = _firm(session)
    with pytest.raises(wa.WhatsappError, match="not on WhatsApp"):
        _order_message(session, t)
    assert session.query(WhatsappMessage).filter_by(tenant_id=t.id).one().status == "failed"


def test_an_unreachable_fleek_leaves_a_failed_message_to_retry(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    fleek.down = True
    t = _firm(session)
    with pytest.raises(wa.WhatsappError, match="Could not reach Fleek"):
        _order_message(session, t)
    row = session.query(WhatsappMessage).filter_by(tenant_id=t.id).one()
    assert row.status == "failed" and "Could not reach Fleek" in (row.error or "")


def test_the_request_body_is_plain_json(session, fleek: FakeFleek) -> None:  # type: ignore[no-untyped-def]
    _order_message(session, _firm(session))
    json.dumps(fleek.calls[0][1])  # nothing in it that cannot travel


# ------------------------------------------------------------------------------ status back from Fleek
EVENTS = "/api/internal/control-plane/whatsapp-events"


def _events(wamid: str, status: str) -> dict:
    st = {"id": wamid, "status": status, "timestamp": "1725800000"}
    return {"entry": [{"changes": [{"value": {"statuses": [st]}}]}]}


def _signed_post(client, payload: dict, *, secret: str = SECRET):  # type: ignore[no-untyped-def]
    from app.services.control_plane_auth import sign

    body = json.dumps(payload).encode()
    stamp = str(int(__import__("time").time()))
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: sign(secret, timestamp=stamp, method="POST", path=EVENTS, body=body),
    }
    return client.post(EVENTS, content=body, headers=headers)


def test_fleeks_status_events_move_the_message_along_through_the_signed_door(session) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import app

    t = _firm(session)
    msg = WhatsappMessage(
        tenant_id=t.id,
        template_name="order_received",
        to_phone="919832011223",
        status="sent",
        wa_message_id="wamid.FLEEK1",
    )
    session.add(msg)
    session.commit()
    client = TestClient(app)
    assert _signed_post(client, _events("wamid.FLEEK1", "delivered")).json() == {"status": "ok"}
    assert _signed_post(client, _events("wamid.FLEEK1", "read")).status_code == 200
    session.refresh(msg)
    assert msg.status == "read" and msg.delivered_at is not None and msg.read_at is not None
    assert (
        _signed_post(client, _events("wamid.NOT-OURS", "delivered")).status_code == 200
    )  # ignored, never an error


def test_the_events_door_refuses_an_unsigned_or_wrongly_signed_call() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.post(EVENTS, json=_events("wamid.X", "delivered")).status_code == 401
    assert _signed_post(client, _events("wamid.X", "delivered"), secret="wrong").status_code == 401

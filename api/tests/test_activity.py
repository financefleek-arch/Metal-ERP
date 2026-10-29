"""What a person does after sign-in is recorded as keys and counts only, the welcome shows once, and the browser can
report only a whitelist (docs/PLAN-shop-activity-intelligence.md). Reuses the sign-up fakes from the onboarding tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActivityEvent, Invoice, User
from app.services import activity
from tests import test_account_onboarding as onboarding
from tests.test_account_onboarding import MOBILE, SIGNUP, FakeFleek, _h, _signup

client = onboarding.client  # the sign-up fakes are fixtures there; pytest finds them here by name
fleek = onboarding.fleek


def _keys() -> list[str]:
    with SessionLocal() as s:
        return [e.key for e in s.scalars(select(ActivityEvent).order_by(ActivityEvent.created_at, ActivityEvent.id))]


def _count(key: str) -> int:
    return _keys().count(key)


def _has_pdf(invoice_id: str, tmp_path: Path) -> None:
    """Rendering is best-effort and may be unavailable under test: point the invoice at a real file."""
    f = tmp_path / "inv.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    with SessionLocal() as s:
        inv = s.get(Invoice, invoice_id)
        assert inv is not None
        inv.pdf_path = str(f)
        s.commit()


def test_the_funnel_milestones_are_recorded_where_they_happen(
    client: TestClient, fleek: FakeFleek, tmp_path: Path
) -> None:
    t = _signup(client, fleek)
    assert _count("signed_up") == 1
    client.patch("/api/tenant", headers=_h(t), json={"address": "12 Main Road", "state_code": "27"})
    client.patch("/api/tenant", headers=_h(t), json={"address": "13 Main Road"})  # not a second milestone
    assert _count("firm_details_saved") == 1

    party = client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"}).json()
    client.post("/api/items", headers=_h(t), json={"name": "Steel plate 10mm", "uom": "kg"})
    inv = client.post(
        "/api/invoices",
        headers=_h(t),
        json={"party_id": party["id"], "lines": [{"description": "Steel plate 10mm", "quantity": "10", "unit_rate": "60", "uom": "kg"}]},
    ).json()
    assert _count("party_created") == 1 and _count("item_created") == 1 and _count("invoice_draft_created") == 1
    assert _count("invoice_finalised") == 0

    assert client.post(f"/api/invoices/{inv['id']}/finalize", headers=_h(t)).status_code == 200
    assert _count("invoice_finalised") == 1
    assert _count("invoice_pdf_out") == 0
    _has_pdf(inv["id"], tmp_path)
    assert client.get(f"/api/invoices/{inv['id']}/pdf", headers=_h(t)).status_code == 200
    assert _count("invoice_pdf_out") == 1  # the PDF left the app

    r = client.post(
        "/api/payments",
        headers=_h(t),
        json={"party_id": party["id"], "amount": "100", "mode": "cash", "allocations": []},
    )
    assert r.status_code == 201, r.text
    assert _count("payment_recorded") == 1


def test_setup_steps_follow_the_activity(client: TestClient, fleek: FakeFleek, tmp_path: Path) -> None:
    t = _signup(client, fleek)
    party = client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"}).json()
    inv = client.post(
        "/api/invoices",
        headers=_h(t),
        json={"party_id": party["id"], "lines": [{"description": "Steel plate", "quantity": "1", "unit_rate": "100", "uom": "kg"}]},
    ).json()
    client.post(f"/api/invoices/{inv['id']}/finalize", headers=_h(t))
    done = {s["key"]: s["done"] for s in client.get("/api/onboarding", headers=_h(t)).json()["steps"]}
    assert done["invoice"] and not done["pdf"]
    _has_pdf(inv["id"], tmp_path)
    client.get(f"/api/invoices/{inv['id']}/pdf", headers=_h(t))
    done = {s["key"]: s["done"] for s in client.get("/api/onboarding", headers=_h(t)).json()["steps"]}
    assert done["pdf"]


def test_signing_in_is_recorded_and_stamps_last_seen(client: TestClient, fleek: FakeFleek) -> None:
    _signup(client, fleek)
    assert _count("signed_in") == 0
    r = client.post("/api/auth/login", json={"identifier": MOBILE, "password": SIGNUP["password"]})
    assert r.status_code == 200
    assert _count("signed_in") == 1
    with SessionLocal() as s:
        assert s.scalar(select(User)).last_seen_at is not None  # type: ignore[union-attr]


def test_last_seen_is_written_at_most_every_few_minutes() -> None:
    now = datetime.now(UTC)
    u = User(tenant_id="t", email="a@b.example", password_hash="x")
    activity.touch_seen(u, now)
    assert u.last_seen_at == now
    activity.touch_seen(u, now + timedelta(minutes=2))
    assert u.last_seen_at == now  # too soon: no second write
    later = now + timedelta(minutes=6)
    activity.touch_seen(u, later)
    assert u.last_seen_at == later


def test_the_browser_may_report_only_whitelisted_keys_with_a_few_short_labels(
    client: TestClient, fleek: FakeFleek
) -> None:
    t = _signup(client, fleek)
    url = "/api/onboarding/activity"
    ok = client.post(url, headers=_h(t), json={"key": "setup_step_clicked", "props": {"step": "invoice"}})
    assert ok.status_code == 204 and _count("setup_step_clicked") == 1
    # a server milestone cannot be forged from the browser, and an unknown key is silently dropped
    for key in ("invoice_finalised", "signed_up", "nonsense"):
        assert client.post(url, headers=_h(t), json={"key": key}).status_code == 204
    assert _count("invoice_finalised") == 0 and _count("signed_up") == 1 and _count("nonsense") == 0

    client.post(
        url,
        headers=_h(t),
        json={"key": "error_shown", "props": {"where": "item_form", "long": "x" * 200, "nested": {"a": 1}, "n": 3}},
    )
    with SessionLocal() as s:
        row = s.scalars(select(ActivityEvent).where(ActivityEvent.key == "error_shown")).one()
        assert row.props == {"where": "item_form", "n": 3}  # the long text and the nested value are dropped


def test_props_never_carry_free_text() -> None:
    assert activity.sanitize({"a": "short", "b": "y" * 41, "c": [1], "d": 2}) == {"a": "short", "d": 2}
    assert len(activity.sanitize({k: 1 for k in "abcdefgh"}) or {}) == 4  # at most four labels
    assert activity.sanitize("nope") is None and activity.sanitize({}) is None


def test_the_welcome_shows_once_to_a_self_signed_up_owner(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    assert client.get("/api/auth/me", headers=_h(t)).json()["show_welcome"] is True
    assert client.post("/api/onboarding/welcome-seen", headers=_h(t)).status_code == 204
    assert client.get("/api/auth/me", headers=_h(t)).json()["show_welcome"] is False
    login = client.post("/api/auth/login", json={"identifier": MOBILE, "password": SIGNUP["password"]}).json()
    assert client.get("/api/auth/me", headers=_h(login["access_token"])).json()["show_welcome"] is False  # not again


def test_the_welcome_is_not_shown_to_a_firm_that_already_invoiced_or_was_not_self_signed(
    client: TestClient, fleek: FakeFleek
) -> None:
    t = _signup(client, fleek)
    party = client.post("/api/parties", headers=_h(t), json={"legal_name": "Anil Traders", "role": "customer"}).json()
    inv = client.post(
        "/api/invoices",
        headers=_h(t),
        json={"party_id": party["id"], "lines": [{"description": "Steel plate", "quantity": "1", "unit_rate": "100", "uom": "kg"}]},
    ).json()
    client.post(f"/api/invoices/{inv['id']}/finalize", headers=_h(t))
    assert client.get("/api/auth/me", headers=_h(t)).json()["show_welcome"] is False

    other = client.post(  # a firm made the old way (operator or Fleek), not by its own sign-up
        "/api/auth/register", json={"firm_name": "Made By Fleek", "email": "ops@made.example.com", "password": "s3cret-pass"}
    ).json()["access_token"]
    assert client.get("/api/auth/me", headers=_h(other)).json()["show_welcome"] is False

"""The Firm page asks for GST details only from shops that bill with GST: the GSTIN fixes the state and PAN, simple
shops are done after an address, and the invoice prints UPI and the account holder. Reuses the sign-up fakes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Tenant
from app.reference import _gstin_check_char
from app.services import onboarding
from tests import test_account_onboarding as onboarding_tests
from tests.test_account_onboarding import FakeFleek, _h, _signup

client = onboarding_tests.client
fleek = onboarding_tests.fleek

_BASE = "23ABCDE1234F1Z"
GSTIN = _BASE + _gstin_check_char(_BASE)


def _tenant(tenant_id: str) -> Tenant:
    with SessionLocal() as s:
        t = s.get(Tenant, tenant_id)
        assert t is not None
        s.expunge(t)
        return t


def _tenant_id(token: str, client: TestClient) -> str:
    return client.get("/api/tenant", headers=_h(token)).json()["id"]


def test_the_gstin_fixes_the_state_and_pan(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    r = client.patch("/api/tenant", headers=_h(t), json={"gst_enabled": True, "gstin": GSTIN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gst_enabled"] is True
    assert body["gstin"] == GSTIN
    assert body["state_code"] == "23"
    assert body["pan"] == GSTIN[2:12]


def test_gst_mode_needs_a_gstin(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    r = client.patch("/api/tenant", headers=_h(t), json={"gst_enabled": True})
    assert r.status_code == 422
    assert "GSTIN" in r.json()["detail"]
    assert client.get("/api/tenant", headers=_h(t)).json()["gst_enabled"] is False


def test_a_bad_gstin_is_refused(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    r = client.patch("/api/tenant", headers=_h(t), json={"gst_enabled": True, "gstin": "23ABCDE1234F1Z0"})
    assert r.status_code == 422


def test_switching_back_to_simple_keeps_the_gstin(client: TestClient, fleek: FakeFleek) -> None:
    t = _signup(client, fleek)
    client.patch("/api/tenant", headers=_h(t), json={"gst_enabled": True, "gstin": GSTIN})
    body = client.patch("/api/tenant", headers=_h(t), json={"gst_enabled": False}).json()
    assert body["gst_enabled"] is False
    assert body["gstin"] == GSTIN


def test_a_simple_shop_is_done_with_an_address_and_a_gst_shop_also_needs_a_state(
    client: TestClient, fleek: FakeFleek
) -> None:
    t = _signup(client, fleek)
    tid = _tenant_id(t, client)
    client.patch("/api/tenant", headers=_h(t), json={"address": "12 Main Road"})
    assert onboarding.firm_details_done(_tenant(tid)) is True

    with SessionLocal() as s:
        tenant = s.get(Tenant, tid)
        assert tenant is not None
        tenant.gst_enabled = True  # as an operator could set it, with no GSTIN yet
        s.commit()
    assert onboarding.firm_details_done(_tenant(tid)) is False
    client.patch("/api/tenant", headers=_h(t), json={"gstin": GSTIN})
    assert onboarding.firm_details_done(_tenant(tid)) is True


def test_the_bill_prints_upi_without_a_bank_account_and_the_account_holder(
    client: TestClient, fleek: FakeFleek
) -> None:
    from pathlib import Path

    html = (Path(__file__).parent.parent / "app" / "templates" / "invoice_v1_nongst.html").read_text(encoding="utf-8")
    upi_at = html.index("tenant.upi_id")
    bank_close = html.index("{% endif %}", html.index("tenant.bank_name or tenant.bank_ac_no"))
    assert upi_at > bank_close, "UPI must be outside the bank block so a UPI-only shop still prints it"
    assert "tenant.bank_holder" in html

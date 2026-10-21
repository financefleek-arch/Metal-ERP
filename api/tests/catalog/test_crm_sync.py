"""Control plane P5b: pushing customers to the Fleek CRM, personal catalog links, consent coming back."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import CustomerOrder, Party, Tenant
from app.models._mixins import WaConsent, WaConsentSource
from app.routers import orders as orders_router
from app.services import crm_sync
from app.services.control_plane_auth import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    check_signature,
    sign,
)
from tests.catalog.test_api_import import CatalogEnv
from tests.catalog.test_customer_orders import LINKS, PHONE, _item, _order

SECRET = "shared-secret"
ORG = "fleek-org-9"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "control_plane_secret", SECRET)
    monkeypatch.setattr(s, "control_plane_url", "http://fleek-backend:8100")


@pytest.fixture
def shop(catalog_client: CatalogEnv) -> tuple[TestClient, dict[str, str], list[dict], str, str]:
    client, h, _ = catalog_client
    items = [_item(client, h, "Beer Mug 480 ML", "1256", pack_qty=6, sku="100500")]
    link = client.post(
        LINKS, headers=h, json={"title": "Range", "selection": {"ids": [i["id"] for i in items]}}
    ).json()
    for limiter in (orders_router._per_ip, orders_router._per_phone):
        limiter.reset()
    tenant_id = client.get("/api/auth/me", headers=h).json()["tenant_id"]
    with SessionLocal() as s:
        s.get(Tenant, tenant_id).fleek_org_id = ORG  # type: ignore[union-attr]
        s.commit()
    return client, h, items, link["token"], tenant_id


def _party(client: TestClient, h: dict[str, str], name: str, phone: str | None = "9876543210") -> str:
    body: dict[str, Any] = {"legal_name": name}
    if phone:
        body["phone"] = phone
    r = client.post("/api/parties", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]  # type: ignore[no-any-return]


def _consent(client: TestClient, h: dict[str, str], pid: str, status: str) -> None:
    r = client.put(f"/api/parties/{pid}/catalog-consent", headers=h, json={"status": status})
    assert r.status_code == 200, r.text


class FakeFleek:
    """Stands in for the CRM door: records each batch and answers with a fixed result per customer."""

    def __init__(self, consent: str = "recorded", status: str = "created") -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.consent, self.status = consent, status
        self.override: dict[str, str] = {}

    def __call__(self, fleek_org_id: str, items: list[dict[str, Any]], client: Any) -> dict[str, Any]:
        assert fleek_org_id == ORG
        self.batches.append(items)
        return {
            "results": [
                {
                    "external_id": i["external_id"],
                    "status": self.status,
                    "consent": self.override.get(i["external_id"], self.consent),
                }
                for i in items
            ]
        }


# --------------------------------------------------------------------------
# what is pushed
# --------------------------------------------------------------------------


def test_every_active_party_with_a_phone_is_pushed_with_its_token_and_consent(shop) -> None:
    client, h, _, _, tid = shop
    yes = _party(client, h, "Ravi Traders")
    _consent(client, h, yes, "opted_in")
    none = _party(client, h, "Seema Stores", "9811111111")
    no = _party(client, h, "Tarun Metals", "9822222222")
    _consent(client, h, no, "opted_in")
    _consent(client, h, no, "opted_out")
    _party(client, h, "No Phone Co", None)
    archived = _party(client, h, "Old Shop", "9833333333")
    client.patch(f"/api/parties/{archived}", headers=h, json={"status": "archived"})

    fake = FakeFleek()
    with SessionLocal() as s:
        out = crm_sync.push(s, s.get(Tenant, tid), sender=fake)  # type: ignore[arg-type]
        s.commit()
    assert out.sent == 3 and out.status == {"created": 3}
    items = {i["external_id"]: i for batch in fake.batches for i in batch}
    assert set(items) == {
        yes,
        none,
        no,
    }  # no criteria on consent: all three go; the CRM's gate decides who is messaged
    assert items[yes]["consent"]["status"] == "opted_in" and items[yes]["consent"]["source"] == "manual"
    assert items[none]["consent"] == {"status": "none"}
    assert items[no]["consent"]["status"] == "opted_out"
    assert all(i["link_token"] and len(i["link_token"]) <= 40 for i in items.values())
    with SessionLocal() as s:
        assert s.get(Party, yes).link_token == items[yes]["link_token"]  # type: ignore[union-attr]
        again = crm_sync.push(s, s.get(Tenant, tid), sender=FakeFleek())  # type: ignore[arg-type]
        assert again.sent == 3
        assert s.get(Party, yes).link_token == items[yes]["link_token"]  # type: ignore[union-attr]


def test_a_large_customer_list_goes_in_batches_of_500(shop) -> None:
    client, h, _, _, tid = shop
    with SessionLocal() as s:
        for i in range(1203):
            s.add(Party(tenant_id=tid, legal_name=f"Customer {i:05d}", phone=f"+9198{i:08d}"))
        s.commit()
    fake = FakeFleek()
    with SessionLocal() as s:
        out = crm_sync.push(s, s.get(Tenant, tid), sender=fake)  # type: ignore[arg-type]
    assert [len(b) for b in fake.batches] == [500, 500, 203] and out.sent == 1203


def test_a_refusal_held_by_the_crm_becomes_an_opt_out_here(shop) -> None:
    client, h, _, _, tid = shop
    pid = _party(client, h, "Ravi Traders")
    _consent(client, h, pid, "opted_in")
    fake = FakeFleek(consent="unchanged")
    fake.override[pid] = "blocked_by_refusal"
    with SessionLocal() as s:
        out = crm_sync.push(s, s.get(Tenant, tid), sender=fake)  # type: ignore[arg-type]
        s.commit()
    assert out.opted_out_here == 1 and out.consent == {"blocked_by_refusal": 1}
    with SessionLocal() as s:
        p = s.get(Party, pid)
        assert p.wa_catalog_consent == WaConsent.opted_out  # type: ignore[union-attr]
        assert p.wa_catalog_consent_source == WaConsentSource.whatsapp_reply  # type: ignore[union-attr]


def test_an_unlinked_or_unconfigured_firm_cannot_push(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, _, _, tid = shop
    with SessionLocal() as s:
        t = s.get(Tenant, tid)
        t.fleek_org_id = None  # type: ignore[union-attr]
        with pytest.raises(crm_sync.SyncError, match="not linked"):
            crm_sync.push(s, t, sender=FakeFleek())  # type: ignore[arg-type]
        t.fleek_org_id = ORG  # type: ignore[union-attr]
        monkeypatch.setattr(get_settings(), "control_plane_url", None)
        with pytest.raises(crm_sync.SyncError, match="not configured"):
            crm_sync.push(s, t)  # type: ignore[arg-type]


def test_the_real_request_is_a_signed_put_and_fleek_errors_are_sync_errors(shop) -> None:
    client, h, _, _, tid = shop
    _party(client, h, "Ravi Traders")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        seen["verdict"] = check_signature(
            SECRET,
            timestamp=request.headers[TIMESTAMP_HEADER],
            signature=request.headers[SIGNATURE_HEADER],
            method=request.method,
            path=request.url.path,
            body=body,
        )
        seen["path"], seen["body"] = request.url.path, json.loads(body)
        return httpx.Response(
            200, json={"results": [{"external_id": "x", "status": "created", "consent": "none"}]}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with SessionLocal() as s:
        crm_sync.push(s, s.get(Tenant, tid), client=http)  # type: ignore[arg-type]
    assert seen["verdict"] is None and seen["path"] == f"/api/internal/metal-erp/orgs/{ORG}/contacts"
    assert seen["body"]["contacts"][0]["name"] == "Ravi Traders"

    for answer in (httpx.Response(409), httpx.Response(200, text="nope"), httpx.Response(200, json={"x": 1})):
        bad = httpx.Client(transport=httpx.MockTransport(lambda r, a=answer: a))
        with SessionLocal() as s, pytest.raises(crm_sync.SyncError):
            crm_sync.push(s, s.get(Tenant, tid), client=bad)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# personal links on the public page and orders
# --------------------------------------------------------------------------


def _token(tid: str, pid: str) -> str:
    with SessionLocal() as s:
        p = s.get(Party, pid)
        tok = crm_sync.ensure_link_token(s, p)  # type: ignore[arg-type]
        s.commit()
        return tok


def test_the_page_greets_a_valid_personal_link_and_ignores_a_bad_one(shop) -> None:
    client, h, _, token, tid = shop
    pid = _party(client, h, "Ravi Traders")
    tok = _token(tid, pid)
    assert client.get(f"/api/public/catalog/{token}").json().get("customer") is None
    assert client.get(f"/api/public/catalog/{token}?p={tok}").json()["customer"] == {"name": "Ravi Traders"}
    assert "customer" not in client.get(f"/api/public/catalog/{token}?p=nope").json()


def test_an_order_from_a_personal_link_is_tied_to_the_customer_and_the_campaign(shop) -> None:
    client, h, items, token, tid = shop
    pid = _party(client, h, "Ravi Traders", "9876500000")
    tok = _token(tid, pid)
    r = _order(token, client, [(items[0]["id"], 1)], phone="", p=tok, c="camp-7", name="Ravi")
    assert r.status_code == 201, r.text  # no phone typed: the one we hold is used
    with SessionLocal() as s:
        o = s.scalar(select(CustomerOrder))
        assert (o.matched_party_id, o.campaign_ref, o.customer_phone) == (pid, "camp-7", "+919876500000")  # type: ignore[union-attr]
    detail = client.get(
        f"/api/orders/{client.get('/api/orders', headers=h).json()[0]['id']}", headers=h
    ).json()
    assert detail["campaign_ref"] == "camp-7" and detail["matched_party_id"] == pid


def test_a_token_from_another_shop_or_an_unknown_one_proves_nothing(shop) -> None:
    client, h, items, token, tid = shop
    r = _order(token, client, [(items[0]["id"], 1)], phone=PHONE, p="not-a-token", c="camp-1")
    assert r.status_code == 201
    with SessionLocal() as s:
        o = s.scalar(select(CustomerOrder))
        assert o.matched_party_id is None and o.campaign_ref == "camp-1"  # type: ignore[union-attr]
    missing_phone = _order(token, client, [(items[0]["id"], 1)], phone="", p="not-a-token")
    assert missing_phone.status_code == 422  # no valid token and no phone: still asked for one


def test_party_by_token_is_scoped_to_the_firm_and_to_active_parties(shop) -> None:
    client, h, _, _, tid = shop
    pid = _party(client, h, "Ravi Traders")
    tok = _token(tid, pid)
    with SessionLocal() as s:
        assert crm_sync.party_by_token(s, tid, tok).id == pid  # type: ignore[union-attr]
        assert crm_sync.party_by_token(s, "another-tenant", tok) is None
        assert crm_sync.party_by_token(s, tid, None) is None
        assert crm_sync.party_by_token(s, tid, "x" * 41) is None
    client.patch(f"/api/parties/{pid}", headers=h, json={"status": "archived"})
    with SessionLocal() as s:
        assert crm_sync.party_by_token(s, tid, tok) is None


# --------------------------------------------------------------------------
# the two doors Fleek calls
# --------------------------------------------------------------------------


def _call(client: TestClient, method: str, path: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    sig = sign(SECRET, timestamp=stamp, method=method, path=path, body=body)
    return client.request(
        method,
        path,
        content=body,
        headers={"Content-Type": "application/json", TIMESTAMP_HEADER: stamp, SIGNATURE_HEADER: sig},
    )


def test_fleek_can_report_an_opt_out_but_never_an_opt_in(shop) -> None:
    client, h, _, _, tid = shop
    pid = _party(client, h, "Ravi Traders")
    _consent(client, h, pid, "opted_in")
    path = "/api/internal/control-plane/consent"
    body = {"fleek_org_id": ORG, "external_id": pid, "status": "opted_out", "at": "2026-10-21T09:00:00+00:00"}
    assert client.put(path, json=body).status_code == 401  # unsigned
    first = _call(client, "PUT", path, body)
    assert first.status_code == 200 and first.json() == {"changed": True}
    assert _call(client, "PUT", path, body).json() == {"changed": False}  # repeating changes nothing
    with SessionLocal() as s:
        assert s.get(Party, pid).wa_catalog_consent == WaConsent.opted_out  # type: ignore[union-attr]
    assert _call(client, "PUT", path, {**body, "status": "opted_in"}).status_code == 422
    assert _call(client, "PUT", path, {**body, "fleek_org_id": "unknown"}).status_code == 404
    assert _call(client, "PUT", path, {**body, "external_id": "no-such-party"}).json() == {"changed": False}


def test_fleek_can_ask_for_a_push_now(shop, monkeypatch: pytest.MonkeyPatch) -> None:
    client, h, _, _, tid = shop
    _party(client, h, "Ravi Traders")
    path = "/api/internal/control-plane/contacts/sync"
    calls: list[str] = []

    def fake_push(session: Any, tenant: Tenant, **kw: Any) -> crm_sync.SyncResult:
        calls.append(tenant.id)
        return crm_sync.SyncResult(sent=1, status={"created": 1}, consent={"none": 1})

    monkeypatch.setattr(crm_sync, "push", fake_push)
    ok = _call(client, "POST", path, {"fleek_org_id": ORG})
    assert ok.status_code == 200 and ok.json()["sent"] == 1 and calls == [tid]
    assert _call(client, "POST", path, {"fleek_org_id": "unknown"}).status_code == 404

    def failing(session: Any, tenant: Tenant, **kw: Any) -> crm_sync.SyncResult:
        raise crm_sync.SyncError("could not reach the control plane")

    monkeypatch.setattr(crm_sync, "push", failing)
    assert _call(client, "POST", path, {"fleek_org_id": ORG}).status_code == 502

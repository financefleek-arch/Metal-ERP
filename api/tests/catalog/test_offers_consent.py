"""Consent to offers on WhatsApp: the order page's second box, and the Parties filter and counts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Party
from app.models._mixins import WaConsent, WaConsentSource
from app.services import crm_sync
from tests.catalog.test_crm_sync import _party, shop  # noqa: F401  (fixture)
from tests.catalog.test_customer_orders import _order


def _consent(pid: str) -> tuple[Any, Any]:
    with SessionLocal() as s:
        p = s.get(Party, pid)
        assert p is not None
        return p.wa_catalog_consent, p.wa_catalog_consent_source


def _token(pid: str) -> str:
    with SessionLocal() as s:
        p = s.get(Party, pid)
        tok = crm_sync.ensure_link_token(s, p)  # type: ignore[arg-type]
        s.commit()
        return tok


def test_a_tick_on_a_personal_link_order_records_the_customers_consent(shop) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, h, items, token, tid = shop
    pid = _party(client, h, "Ravi Traders", "9876500000")
    r = _order(token, client, [(items[0]["id"], 1)], phone="", p=_token(pid), name="Ravi", offers_opt_in=True)
    assert r.status_code == 201, r.text
    assert _consent(pid) == (WaConsent.opted_in, WaConsentSource.order_page)


def test_an_unticked_box_says_nothing_and_never_opts_anyone_out(shop) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, h, items, token, tid = shop
    pid = _party(client, h, "Ravi Traders", "9876500000")
    assert (
        _order(token, client, [(items[0]["id"], 1)], phone="", p=_token(pid), name="Ravi").status_code == 201
    )
    assert _consent(pid)[0] == WaConsent.none
    # someone who already agreed at the counter stays agreed when a later order leaves the box unticked
    assert (
        client.put(f"/api/parties/{pid}/catalog-consent", headers=h, json={"status": "opted_in"}).status_code
        == 200
    )
    assert (
        _order(token, client, [(items[0]["id"], 2)], phone="", p=_token(pid), name="Ravi").status_code == 201
    )
    assert _consent(pid)[0] == WaConsent.opted_in


def test_a_tick_from_a_stranger_waits_on_the_order_until_the_shop_ties_it_to_a_customer(shop) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, h, items, token, tid = shop
    r = _order(token, client, [(items[0]["id"], 1)], offers_opt_in=True)
    assert r.status_code == 201, r.text
    oid = client.get("/api/orders", headers=h).json()[0]["id"]
    assert client.get(f"/api/orders/{oid}", headers=h).json()["offers_opt_in"] is True
    made = client.post(f"/api/orders/{oid}/customer", headers=h, json={"create_new": True})
    assert made.status_code == 200, made.text
    assert _consent(made.json()["matched_party_id"]) == (WaConsent.opted_in, WaConsentSource.order_page)


def test_an_unticked_order_leaves_a_new_customer_not_asked(shop) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, h, items, token, tid = shop
    _order(token, client, [(items[0]["id"], 1)])
    oid = client.get("/api/orders", headers=h).json()[0]["id"]
    assert client.get(f"/api/orders/{oid}", headers=h).json()["offers_opt_in"] is False
    pid = client.post(f"/api/orders/{oid}/customer", headers=h, json={"create_new": True}).json()[
        "matched_party_id"
    ]
    assert _consent(pid)[0] == WaConsent.none


def test_the_parties_list_filters_by_answer_and_the_counts_only_cover_customers_with_a_phone(shop) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, h, items, token, tid = shop
    yes = _party(client, h, "Agreed Traders", "9876500001")
    no = _party(client, h, "Refused Traders", "9876500002")
    _party(client, h, "Silent Traders", "9876500003")
    client.post("/api/parties", headers=h, json={"legal_name": "No Phone Traders", "role": "customer"})
    assert (
        client.put(f"/api/parties/{yes}/catalog-consent", headers=h, json={"status": "opted_in"}).status_code
        == 200
    )
    assert (
        client.put(f"/api/parties/{no}/catalog-consent", headers=h, json={"status": "opted_out"}).status_code
        == 200
    )

    def names(flt: str) -> list[str]:
        return sorted(p["legal_name"] for p in client.get(f"/api/parties?offers={flt}", headers=h).json())

    assert names("agreed") == ["Agreed Traders"] and names("opted_out") == ["Refused Traders"]
    assert "Silent Traders" in names("not_asked") and "Agreed Traders" not in names("not_asked")
    counts = client.get("/api/parties/offers-counts", headers=h).json()
    assert counts["agreed"] == 1 and counts["opted_out"] == 1 and counts["not_asked"] >= 1
    # a customer with no phone is not someone we could send to, so adding one changes nothing
    client.post("/api/parties", headers=h, json={"legal_name": "Another No Phone", "role": "customer"})
    assert client.get("/api/parties/offers-counts", headers=h).json() == counts
    assert client.get("/api/parties?offers=nope", headers=h).status_code == 422
    assert client.get("/api/parties/offers-counts").status_code in (401, 403)
    with SessionLocal() as s:
        assert s.scalar(select(Party.id).where(Party.legal_name == "No Phone Traders")) is not None

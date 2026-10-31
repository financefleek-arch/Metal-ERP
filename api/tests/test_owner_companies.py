"""S1.4 - the owner manages the firm's Tally companies (routers/tally_companies.py).

Decision 2026-10-31: owners add and edit their own companies; the accountant keeps the link healthy;
nobody else sees what they may not."""

# ruff: noqa: F811
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import BackupShop, User
from app.security import hash_password
from app.services.tally.companies import add_company, get_default_company
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)

BASE = "/api/tally/companies"


def _h(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _agent(tid: str) -> str:
    with SessionLocal() as s:
        shop = BackupShop(name="Shop", api_key_hash=f"k-{tid}", tenant_id=tid)
        s.add(shop)
        s.commit()
        return shop.id


def _colleague(client: TestClient, tid: str, role: str) -> dict[str, str]:
    email = f"{role}-{tid[:6]}@x.example.com"
    with SessionLocal() as s:
        s.add(User(tenant_id=tid, email=email, password_hash=hash_password("s3cret-pass"), role=role))
        s.commit()
    r = client.post("/api/auth/login", json={"email": email, "password": "s3cret-pass"})
    assert r.status_code == 200, r.text
    return _h(r.json()["access_token"])


def test_adding_needs_the_agent_first(client: TestClient) -> None:
    tok, _tid = _register_firm(client, "oc1@x.example.com")
    r = client.post(BASE, headers=_h(tok), json={"company_name": "Shah Traders"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "agent_missing"


def test_the_owner_adds_edits_and_switches_off(client: TestClient) -> None:
    tok, tid = _register_firm(client, "oc2@x.example.com")
    _agent(tid)
    first = client.post(BASE, headers=_h(tok), json={"company_name": "Shah Traders"})
    assert first.status_code == 201, first.text
    assert first.json()["is_default"] is True and first.json()["series"] == "Sales"
    second = client.post(
        BASE, headers=_h(tok), json={"company_name": "Shah Estimates", "kind": "estimate"}
    ).json()
    assert second["is_default"] is False and second["series"] == "EST" and second["connected"]

    # a duplicate name (any case or spacing) is refused
    dup = client.post(BASE, headers=_h(tok), json={"company_name": "  shah  traders "})
    assert dup.status_code == 422 and "already" in dup.json()["detail"]

    patched = client.patch(
        f"{BASE}/{second['id']}",
        headers=_h(tok),
        json={"document_label": "Estimate", "series_prefix": "ES", "include_in_reminders": False,
              "visible_roles": ["accountant"]},
    ).json()
    assert patched["series"] == "ES" and patched["document_label"] == "Estimate"
    assert patched["include_in_reminders"] is False and patched["visible_roles"] == ["accountant"]
    # a prefix another company already uses, or the default's "Sales", is refused
    taken = client.patch(f"{BASE}/{first.json()['id']}", headers=_h(tok), json={"series_prefix": "es"})
    assert taken.status_code == 422
    assert client.patch(
        f"{BASE}/{second['id']}", headers=_h(tok), json={"series_prefix": "Sales"}
    ).status_code == 422
    assert client.patch(
        f"{BASE}/{second['id']}", headers=_h(tok), json={"visible_roles": ["boss"]}
    ).status_code == 422

    # the default cannot be switched off; another can, and can come back
    first_id = first.json()["id"]
    assert client.post(f"{BASE}/{first_id}/active", headers=_h(tok), json={"active": False}).status_code == 422
    off = client.post(f"{BASE}/{second['id']}/active", headers=_h(tok), json={"active": False}).json()
    assert off["status"] == "disabled"
    assert client.post(f"{BASE}/{second['id']}/active", headers=_h(tok), json={"active": True}).json()["status"] == "active"

    made = client.post(f"{BASE}/{second['id']}/default", headers=_h(tok)).json()
    assert made["is_default"] is True
    with SessionLocal() as s:
        assert get_default_company(s, tid).id == second["id"]


def test_a_rename_forgets_the_old_match(client: TestClient) -> None:
    tok, tid = _register_firm(client, "oc3@x.example.com")
    _agent(tid)
    c = client.post(BASE, headers=_h(tok), json={"company_name": "Shah Traders"}).json()
    with SessionLocal() as s:
        from app.models import TallyCompany

        row = s.get(TallyCompany, c["id"])
        row.company_guid, row.verify_state = "guid-1", "verified"
        s.commit()
    after = client.patch(f"{BASE}/{c['id']}", headers=_h(tok), json={"company_name": "Shah 27-28"}).json()
    assert after["verify_state"] is None and after["company_name"] == "Shah 27-28"


def test_each_role_sees_and_does_only_its_part(client: TestClient) -> None:
    tok, tid = _register_firm(client, "oc4@x.example.com")
    _agent(tid)
    open_co = client.post(BASE, headers=_h(tok), json={"company_name": "Open Books"}).json()
    with SessionLocal() as s:
        private = add_company(s, tid, "Private", kind="other")
        private.visible_roles = ["owner"]  # a stored owner-only company
        s.commit()
        private_id = private.id
    acct = _colleague(client, tid, "accountant")
    counter = _colleague(client, tid, "counter")

    # list: the owner sees both, an accountant and a counter only what is open to them
    assert {c["company_name"] for c in client.get(BASE, headers=_h(tok)).json()} == {"Open Books", "Private"}
    seen = client.get(BASE, headers=acct).json()
    assert [c["company_name"] for c in seen] == ["Open Books"] and seen[0]["visible_roles"] is None
    assert [c["company_name"] for c in client.get(BASE, headers=counter).json()] == ["Open Books"]

    # only the owner adds, edits, defaults or switches off
    for headers in (acct, counter):
        assert client.post(BASE, headers=headers, json={"company_name": "X"}).status_code == 403
        assert client.patch(f"{BASE}/{open_co['id']}", headers=headers, json={"kind": "branch"}).status_code == 403
        assert client.post(f"{BASE}/{open_co['id']}/default", headers=headers).status_code == 403
        assert client.post(f"{BASE}/{open_co['id']}/active", headers=headers, json={"active": False}).status_code == 403

    # an accountant keeps the link healthy: ledger names yes, a hidden company is not even found
    ledger = client.put(f"{BASE}/{open_co['id']}/ledger-map", headers=acct, json={"cash_ledger": "Cash"})
    assert ledger.status_code == 200 and ledger.json()["ledger_map"] == {"cash_ledger": "Cash"}
    assert client.put(f"{BASE}/{private_id}/ledger-map", headers=acct, json={"cash_ledger": "x"}).status_code == 404
    # a counter cannot touch the link at all
    assert client.put(f"{BASE}/{open_co['id']}/ledger-map", headers=counter, json={"cash_ledger": "x"}).status_code == 403
    # another firm's company id finds nothing
    other_tok, _ = _register_firm(client, "oc4b@x.example.com")
    assert client.patch(f"{BASE}/{open_co['id']}", headers=_h(other_tok), json={"kind": "branch"}).status_code == 404

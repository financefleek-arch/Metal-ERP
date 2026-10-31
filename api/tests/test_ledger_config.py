"""S1.5 - ledger names come from the document's company, in one place (services/tally/ledger_config.py)."""

# ruff: noqa: F811
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services.tally.companies import add_company
from app.services.tally.ledger_config import ledger_config, save_ledger_config
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)


def test_a_name_the_map_lacks_takes_the_standard_one_and_others_are_kept(client: TestClient) -> None:
    _tok, tid = _register_firm(client, "lc1@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        c.ledger_map = {"purchase_ledger": "Purchases 18%", "sales_ledger": "Sales"}
        cfg = ledger_config(c)
        assert cfg.purchase_ledger == "Purchases 18%"
        assert cfg.cgst_ledger == "CGST" and cfg.creditors_group == "Sundry Creditors"
        assert ledger_config(c, xml_encoding="UTF-8").xml_encoding == "UTF-8"
        save_ledger_config(c, {"cgst_ledger": "Output CGST", "creditors_group": "Suppliers"})
        assert c.ledger_map["cgst_ledger"] == "Output CGST"
        assert c.ledger_map["creditors_parent"] == "Suppliers"
        assert c.ledger_map["sales_ledger"] == "Sales"  # untouched


def test_two_companies_keep_their_own_names(client: TestClient) -> None:
    _tok, tid = _register_firm(client, "lc2@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        save_ledger_config(gst, {"purchase_ledger": "Purchase GST"})
        save_ledger_config(est, {"purchase_ledger": "Purchase Estimate"})
        assert ledger_config(gst).purchase_ledger == "Purchase GST"
        assert ledger_config(est).purchase_ledger == "Purchase Estimate"


def test_the_settings_api_reads_and_writes_the_default_company(client: TestClient) -> None:
    from app.services.tally.companies import get_default_company
    from tests.inward.conftest import auth, enable_inward_flag, register

    tok = register(client, "lc3@x.example.com")
    enable_inward_flag(client, tok)
    h = auth(tok)
    me = client.get("/api/auth/me", headers=h).json()
    tid = me.get("tenant_id") or me["tenant"]["id"]
    with SessionLocal() as s:
        add_company(s, tid, "GST")
        s.commit()
    got = client.get("/api/inward-bills/settings/ledgers", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["purchase_ledger"] == "Purchase Accounts"
    body = got.json() | {"purchase_ledger": "Purchase 5%"}
    assert client.put("/api/inward-bills/settings/ledgers", headers=h, json=body).status_code == 200
    again = client.get("/api/inward-bills/settings/ledgers", headers=h).json()
    assert again["purchase_ledger"] == "Purchase 5%"
    with SessionLocal() as s:
        assert get_default_company(s, tid).ledger_map["purchase_ledger"] == "Purchase 5%"

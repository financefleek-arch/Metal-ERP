"""S2.1 / S2.2 / S2.5 / S2.6 - a document posts to its own Tally company."""

# ruff: noqa: F811
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Invoice, Party
from app.services.tally.companies import add_company, get_default_company
from app.services.tally.company_routing import company_for_document, resolve_company
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_tally_links import _party


def _h(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def test_the_order_explicit_then_customer_then_firm(client: TestClient) -> None:
    _tok, tid = _register_firm(client, "dc1@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        other = add_company(s, tid, "Branch", kind="branch")
        p = _party(s, tid)
        assert resolve_company(s, tid, party=p).id == gst.id  # firm default
        p.default_company_id = est.id
        assert resolve_company(s, tid, party=p).id == est.id  # the customer's
        assert resolve_company(s, tid, explicit_id=other.id, party=p).id == other.id  # explicit wins
        est.status = "disabled"  # a disabled customer default is skipped
        assert resolve_company(s, tid, party=p).id == gst.id


def test_a_draft_takes_the_customers_company_and_it_follows_a_changed_customer(
    client: TestClient,
) -> None:
    tok, tid = _register_firm(client, "dc2@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        cash = _party(s, tid, "Cash Customer")
        credit = _party(s, tid, "Credit Customer")
        credit.default_company_id = est.id
        s.commit()
        gst_id, est_id, cash_id, credit_id = gst.id, est.id, cash.id, credit.id

    made = client.post("/api/invoices", headers=_h(tok), json={"party_id": credit_id}).json()
    assert made["tally_company_id"] == est_id and made["tally_company_name"] == "Estimate"
    # defaulted, so it follows the customer when that changes
    moved = client.put(
        f"/api/invoices/{made['id']}", headers=_h(tok), json={"party_id": cash_id}
    ).json()
    assert moved["tally_company_id"] == gst_id
    # chosen explicitly, so it stays when the customer changes
    pinned = client.put(
        f"/api/invoices/{made['id']}", headers=_h(tok), json={"tally_company_id": est_id}
    ).json()
    assert pinned["tally_company_id"] == est_id
    back = client.put(
        f"/api/invoices/{made['id']}", headers=_h(tok), json={"party_id": credit_id}
    ).json()
    assert back["tally_company_id"] == est_id


def test_an_unknown_or_hidden_company_cannot_be_chosen(client: TestClient) -> None:
    tok, tid = _register_firm(client, "dc3@x.example.com")
    r = client.post("/api/invoices", headers=_h(tok), json={"tally_company_id": "nope"})
    assert r.status_code == 422
    with SessionLocal() as s:
        add_company(s, tid, "GST")
        s.commit()
    assert client.post("/api/invoices", headers=_h(tok), json={}).json()["tally_company_name"] == "GST"


def test_a_push_goes_to_the_documents_company_not_the_default(client: TestClient) -> None:
    from app.services.tally.push_readiness import push_blockers

    _tok, tid = _register_firm(client, "dc4@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        s.flush()
        gst.ledger_map = {"sales_ledger": "Sales"}
        inv = Invoice(
            tenant_id=tid, fy="2026-27", date=__import__("datetime").date(2026, 10, 1),
            tally_company_id=est.id,
        )
        s.add(inv)
        s.flush()
        assert company_for_document(s, inv).id == est.id
        codes = {b.code for b in push_blockers(s, inv)}
        # the estimate company has no sales ledger mapped: the GST one's map must not leak in
        assert "ledger_map_incomplete" in codes
        inv.tally_company_id = None  # no company of its own: the firm default
        assert company_for_document(s, inv).id == get_default_company(s, tid).id == gst.id
        est.status = "disabled"
        inv.tally_company_id = est.id
        assert "company_disabled" in {b.code for b in push_blockers(s, inv)}


def test_party_default_company_is_validated_and_saved(client: TestClient) -> None:
    tok, tid = _register_firm(client, "dc5@x.example.com")
    with SessionLocal() as s:
        est = add_company(s, tid, "Estimate", kind="estimate")
        p = _party(s, tid)
        s.commit()
        pid, est_id = p.id, est.id
    bad = client.patch(f"/api/parties/{pid}", headers=_h(tok), json={"default_company_id": "nope"})
    assert bad.status_code == 422
    ok = client.patch(f"/api/parties/{pid}", headers=_h(tok), json={"default_company_id": est_id})
    assert ok.status_code == 200 and ok.json()["default_company_id"] == est_id
    cleared = client.patch(f"/api/parties/{pid}", headers=_h(tok), json={"default_company_id": None})
    assert cleared.json()["default_company_id"] is None
    with SessionLocal() as s:
        assert s.get(Party, pid).default_company_id is None


# --------------------------------------------------------------------------
# S2.4 - number series and label per company
# --------------------------------------------------------------------------


def test_series_names_are_unique_and_the_default_keeps_sales(client: TestClient) -> None:
    from app.services.tally.companies import series_for, series_map

    _tok, tid = _register_firm(client, "dc6@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        est2 = add_company(s, tid, "Estimate 2", kind="estimate")
        own = add_company(s, tid, "Own", kind="branch")
        own.series_prefix = "KHD"
        m = series_map(s, tid)
        assert m[gst.id] == "Sales"  # a firm's existing numbers carry on
        assert m[est.id] == "EST" and m[est2.id] == "EST2"  # never shared
        assert m[own.id] == "KHD"
        assert series_for(s, None) == "Sales"


def test_each_company_numbers_its_own_bills(client: TestClient) -> None:
    from app.models import Invoice
    from tests.test_invoice_tally_push_integration import _party as _api_party

    tok, tid = _register_firm(client, "dc7@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        est.document_label = "Estimate"
        s.commit()
        gst_id, est_id = gst.id, est.id
    pid = _api_party(client, _h(tok))

    def bill(company_id: str) -> str:
        d = client.post(
            "/api/invoices", headers=_h(tok), json={"party_id": pid, "tally_company_id": company_id}
        ).json()
        client.put(
            f"/api/invoices/{d['id']}", headers=_h(tok),
            json={"lines": [{"description": "SS Thali", "quantity": "1", "unit_rate": "10"}]},
        )
        assert client.post(f"/api/invoices/{d['id']}/finalize", headers=_h(tok)).status_code == 200
        return d["id"]

    a, b, c = bill(gst_id), bill(est_id), bill(gst_id)
    with SessionLocal() as s:
        rows = {i: s.get(Invoice, i) for i in (a, b, c)}
        assert (rows[a].series, rows[a].number) == ("Sales", 1)
        assert (rows[b].series, rows[b].number) == ("EST", 1)  # its own sequence, not 2
        assert (rows[c].series, rows[c].number) == ("Sales", 2)


def test_a_final_bill_in_a_multi_company_firm_says_what_to_do_if_the_company_is_wrong(
    client: TestClient,
) -> None:
    from tests.test_invoice_tally_push_integration import _party as _api_party

    tok, tid = _register_firm(client, "dc8@x.example.com")
    with SessionLocal() as s:
        add_company(s, tid, "GST")
        s.commit()
    pid = _api_party(client, _h(tok))
    d = client.post("/api/invoices", headers=_h(tok), json={"party_id": pid}).json()
    assert d["company_guidance"] is None  # a draft can still be changed
    client.put(
        f"/api/invoices/{d['id']}", headers=_h(tok),
        json={"lines": [{"description": "SS Thali", "quantity": "1", "unit_rate": "10"}]},
    )
    client.post(f"/api/invoices/{d['id']}/finalize", headers=_h(tok))
    one = client.get(f"/api/invoices/{d['id']}", headers=_h(tok)).json()
    assert one["company_guidance"] is None  # one company: nothing to get wrong
    with SessionLocal() as s:
        add_company(s, tid, "Estimate", kind="estimate")
        s.commit()
    two = client.get(f"/api/invoices/{d['id']}", headers=_h(tok)).json()
    assert "credit note" in two["company_guidance"] and "GST" in two["company_guidance"]

"""S4.4 - a recorded payment pushed to Tally as a Receipt voucher (services/tally/receipts.py).

The XML shape is the one posted live to TallyPrime on 2026-10-31 (vouchers 29 and 30)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentOutboxItem, Invoice, Payment, PaymentAllocation, TallyLink, TallySyncJob
from app.services.tally.companies import add_company
from app.services.tally.push import process_push_result
from app.services.tally.receipts import (
    Part,
    build_receipt_envelope,
    receipt_reference,
    split_by_company,
)
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _admin_token,
    _finalized_invoice,
    _h,
    _link_item_to_tally,
    _link_party_to_tally,
    _mark_agent_online_and_connected,
    _party,
    _provision_agent,
    _register_firm,
    _stub_installer_build,
    client,
)


def _payment(voucher_no: int | None = 7) -> Payment:
    return Payment(
        id="pay-1", tenant_id="t", party_id="p", date=date(2026, 10, 1), amount=Decimal("50.00"),
        mode="cash", voucher_no=voucher_no, ref_no=None,
    )


def test_a_receipt_against_a_bill_has_the_live_verified_shape() -> None:
    env = build_receipt_envelope(
        _payment(),
        [Part("inv-1", "9104", Decimal("40.00")), Part(None, None, Decimal("10.00"))],
        party_name="ZZTEST Customer",
        cash_or_bank_ledger="Cash",
        company_name="Fleek",
    )
    assert env.find(".//SVCURRENTCOMPANY").text == "Fleek"
    v = env.find(".//VOUCHER")
    assert v.get("VCHTYPE") == "Receipt" and v.get("ACTION") == "Create"
    assert env.find(".//DATE").text == "20261001"
    assert env.find(".//REFERENCE").text == "ME/R/7"
    party, cash = env.findall(".//ALLLEDGERENTRIES.LIST")
    # the party is credited (positive, not deemed positive), the cash ledger debited
    assert party.findtext("LEDGERNAME") == "ZZTEST Customer"
    assert party.findtext("ISDEEMEDPOSITIVE") == "No" and party.findtext("AMOUNT") == "50.00"
    assert cash.findtext("LEDGERNAME") == "Cash"
    assert cash.findtext("ISDEEMEDPOSITIVE") == "Yes" and cash.findtext("AMOUNT") == "-50.00"
    first, second = party.findall("BILLALLOCATIONS.LIST")
    assert (first.findtext("NAME"), first.findtext("BILLTYPE"), first.findtext("AMOUNT")) == (
        "9104",
        "Agst Ref",
        "40.00",
    )
    # unapplied money is an advance under our own receipt number
    assert (second.findtext("NAME"), second.findtext("BILLTYPE")) == ("ME/R/7", "New Ref")


def test_a_receipt_needs_an_allocation() -> None:
    with pytest.raises(ValueError):
        build_receipt_envelope(_payment(), [], party_name="X", cash_or_bank_ledger="Cash")


def _setup(client: TestClient, email: str):
    tok, tid = _register_firm(client, email)
    admin = _admin_token(client)
    shop_id = _provision_agent(client, admin, tid)
    client.post(
        f"/api/admin/firms/{tid}/tally/company", headers=_h(admin), json={"company_name": "Fleek"}
    )
    client.put(
        f"/api/admin/firms/{tid}/tally/company/ledger-map",
        headers=_h(admin),
        json={"sales_ledger": "Sales Accounts", "cash_ledger": "Cash", "bank_ledger": "HDFC Bank"},
    )
    pid = _party(client, _h(tok))
    iid, item_id = _finalized_invoice(client, _h(tok), pid)
    _link_party_to_tally(pid)
    _link_item_to_tally(item_id)
    _mark_agent_online_and_connected(shop_id)
    return tok, tid, pid, iid, shop_id


def _pay(client, tok, pid, iid, amount, *, on_account="0", mode="cash") -> str:
    body = {
        "party_id": pid,
        "amount": str(Decimal(amount) + Decimal(on_account)),
        "mode": mode,
        "allocations": [{"invoice_id": iid, "type": "against_invoice", "amount": amount}],
    }
    if Decimal(on_account) > 0:
        body["allocations"].append({"type": "on_account", "amount": on_account})
    r = client.post("/api/payments", headers=_h(tok), json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _xml_of(job_id: str) -> str:
    with SessionLocal() as s:
        for ob in s.scalars(select(AgentOutboxItem)):
            if ob.payload.get("job_id") == job_id:
                return ob.payload["voucher_xml"]
    raise AssertionError("no outbox item for the job")


def test_a_payment_is_pushed_and_links_with_the_real_voucher_guid(client: TestClient) -> None:
    tok, tid, pid, iid, shop_id = _setup(client, "rc1@x.example.com")
    pay_id = _pay(client, tok, pid, iid, "100.00", on_account="25.00")

    status_ = client.get(f"/api/tally/payments/{pay_id}/push-status", headers=_h(tok)).json()
    assert status_["pushable"] is True, status_
    r = client.post(f"/api/tally/payments/{pay_id}/push", headers=_h(tok))
    assert r.status_code == 201, r.text
    (job,) = r.json()
    assert job["kind"] == "push_receipt" and job["entity_type"] == "payment"

    xml = _xml_of(job["id"])
    assert 'VCHTYPE="Receipt"' in xml and "<BILLTYPE>Agst Ref</BILLTYPE>" in xml
    assert "<BILLTYPE>New Ref</BILLTYPE>" in xml and "<LEDGERNAME>Cash</LEDGERNAME>" in xml
    with SessionLocal() as s:
        action = next(
            ob.payload["action"] for ob in s.scalars(select(AgentOutboxItem))
            if ob.payload.get("job_id") == job["id"]
        )
    assert action == "push_sales"  # the agent's own transport action: no agent release needed

    ok = "<RESPONSE><CREATED>1</CREATED><ERRORS>0</ERRORS><LASTVCHID>29</LASTVCHID></RESPONSE>"
    with SessionLocal() as s:
        process_push_result(
            s, s.get(TallySyncJob, job["id"]), ok=True, tally_response=ok, agent_error=None
        )
        s.commit()
        link = s.scalar(
            select(TallyLink).where(
                TallyLink.entity_type == "payment", TallyLink.entity_id == pay_id
            )
        )
        assert link is not None and link.tally_masterid == "29"
        assert s.get(TallySyncJob, job["id"]).status == "ok"

    # unchanged, so pushing again hands back the finished job and queues nothing new
    again = client.post(f"/api/tally/payments/{pay_id}/push", headers=_h(tok))
    assert again.status_code == 201 and again.json()[0]["id"] == job["id"]


def test_bank_modes_use_the_bank_ledger(client: TestClient) -> None:
    tok, tid, pid, iid, _shop = _setup(client, "rc2@x.example.com")
    pay_id = _pay(client, tok, pid, iid, "100.00", mode="upi")
    (job,) = client.post(f"/api/tally/payments/{pay_id}/push", headers=_h(tok)).json()
    assert "<LEDGERNAME>HDFC Bank</LEDGERNAME>" in _xml_of(job["id"])


def test_a_missing_ledger_or_unknown_payment_blocks(client: TestClient) -> None:
    tok, tid, pid, iid, _shop = _setup(client, "rc3@x.example.com")
    admin = _admin_token(client)
    client.put(
        f"/api/admin/firms/{tid}/tally/company/ledger-map",
        headers=_h(admin),
        json={"cash_ledger": ""},
    )
    pay_id = _pay(client, tok, pid, iid, "100.00")
    blocked = client.get(f"/api/tally/payments/{pay_id}/push-status", headers=_h(tok)).json()
    assert [b["code"] for b in blocked["blockers"]] == ["ledger_map_incomplete"]
    assert client.post(f"/api/tally/payments/{pay_id}/push", headers=_h(tok)).status_code == 422
    assert client.get("/api/tally/payments/nope/push-status", headers=_h(tok)).status_code == 404


def test_a_payment_across_two_companies_splits_by_the_invoices_company(client: TestClient) -> None:
    tok, tid, pid, iid, _shop = _setup(client, "rc4@x.example.com")
    with SessionLocal() as s:
        est = add_company(s, tid, "Estimate", kind="estimate")
        s.commit()
        est_id = est.id
    iid2, _ = _finalized_invoice(client, _h(tok), pid, description="Other Thing")
    with SessionLocal() as s:
        s.get(Invoice, iid2).tally_company_id = est_id
        s.commit()
    pay_id = _pay(client, tok, pid, iid, "100.00")
    with SessionLocal() as s:
        pay = s.get(Payment, pay_id)
        s.add(
            PaymentAllocation(
                payment_id=pay.id, invoice_id=iid2, type="against_invoice", amount=Decimal("30.00")
            )
        )
        s.flush()
        s.refresh(pay)
        split = split_by_company(s, pay)
        totals = {sum(p.amount for p in parts) for parts in split.values()}
        assert len(split) == 2 and totals == {Decimal("100.00"), Decimal("30.00")}
    assert receipt_reference(_payment()) == "ME/R/7"

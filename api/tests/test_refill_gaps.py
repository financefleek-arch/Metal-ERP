"""Deleted-in-Tally detection, pending-bill dates, and the hand-typed-vs-Tally review (services/tally/refill.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ItemTallyState, PartyTallySnapshot
from app.routers import tally_companies
from app.services.tally import refill
from app.services.tally.companies import add_company
from app.services.tally.light_masters import ItemRow
from app.services.tally.links import record_master_guid
from app.services.tally.refill import (
    DifferenceError,
    PartyBalance,
    apply_bill_dates,
    apply_outstanding,
    apply_stock,
    differences,
    settle_difference,
)
from app.services.tally.refill_job import plan_steps
from app.services.tally.voucher_xml import PendingBill, parse_bills_receivable
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_owner_companies import _agent, _h
from tests.test_tally_links import _item, _party

D = Decimal
FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 31)


def _row(guid: str, name: str, qty: str = "5") -> ItemRow:
    return ItemRow(guid=guid, name=name, group="", unit="Pcs", qty=D(qty), qty_unit="Pcs", rate=D("10"), value=D("50"))


def _firm(client: TestClient, email: str) -> str:
    return _register_firm(client, email)[1]


# --------------------------------------------------------------------- gone from Tally


def test_an_item_missing_from_a_complete_read_is_listed_and_its_stock_cleared(client: TestClient) -> None:
    tid = _firm(client, "rg1@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        a, b = _item(s, tid, "Kept"), _item(s, tid, "Deleted In Tally")
        record_master_guid(s, tid, c, "item", a, "g-a", name="Kept")
        record_master_guid(s, tid, c, "item", b, "g-b", name="Deleted In Tally")
        apply_stock(s, c, [_row("g-a", "Kept"), _row("g-b", "Deleted In Tally")], complete=True)
        res = apply_stock(s, c, [_row("g-a", "Kept")], complete=True)
        s.commit()
        assert res.missing == ["Deleted In Tally"] and not res.missing_skipped
        st = {x.item_id: x for x in s.scalars(select(ItemTallyState))}
        assert st[b.id].stock_qty is None and st[b.id].stock_as_of is None
        assert st[a.id].stock_qty == D(5)


def test_a_read_of_changes_never_calls_anything_missing(client: TestClient) -> None:
    tid = _firm(client, "rg2@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        a, b = _item(s, tid, "A"), _item(s, tid, "B")
        record_master_guid(s, tid, c, "item", a, "g-a", name="A")
        record_master_guid(s, tid, c, "item", b, "g-b", name="B")
        res = apply_stock(s, c, [_row("g-a", "A")], complete=False)
        assert res.missing == []


def test_most_of_the_list_vanishing_at_once_is_a_bad_read_and_is_left_alone(client: TestClient) -> None:
    tid = _firm(client, "rg3@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        rows = []
        for i in range(30):
            it = _item(s, tid, f"Item {i}")
            record_master_guid(s, tid, c, "item", it, f"g-{i}", name=f"Item {i}")
            rows.append(_row(f"g-{i}", f"Item {i}"))
        apply_stock(s, c, rows, complete=True)
        res = apply_stock(s, c, rows[:5], complete=True)  # 25 of 30 "vanished"
        assert res.missing == [] and res.missing_skipped is True
        assert sum(1 for x in s.scalars(select(ItemTallyState)) if x.stock_qty is not None) == 30


def test_a_party_missing_from_the_ledger_list_loses_its_balance_not_its_opening_figure(
    client: TestClient,
) -> None:
    tid = _firm(client, "rg4@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        stay, gone = _party(s, tid, "Stays"), _party(s, tid, "Gone")
        record_master_guid(s, tid, c, "party", stay, "p-1", name="Stays")
        record_master_guid(s, tid, c, "party", gone, "p-2", name="Gone")
        both = [PartyBalance("Stays", D("10"), guid="p-1"), PartyBalance("Gone", D("20"), guid="p-2")]
        apply_outstanding(s, c, both, TODAY, absent_is_zero=False, complete=True)
        s.refresh(gone)
        assert gone.opening_balance == D("20")
        res = apply_outstanding(s, c, both[:1], TODAY, absent_is_zero=False, complete=True)
        s.refresh(gone)
        assert res.missing == ["Gone"] and gone.opening_balance == D("20")  # what was typed or filled stays
        snaps = {x.party_id: x for x in s.scalars(select(PartyTallySnapshot))}
        assert snaps[gone.id].outstanding is None and snaps[stay.id].outstanding == D("10.00")


# ------------------------------------------------------------------------- pending bills


def test_pending_bills_give_the_oldest_date_and_count_per_party(client: TestClient) -> None:
    tid = _firm(client, "rg5@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "ZZTEST Customer")
        res = apply_bill_dates(s, c, parse_bills_receivable((FIX / "tally_bills_receivable_dev_sample.xml").read_bytes()))
        snap = s.scalar(select(PartyTallySnapshot))
        assert snap.party_id == p.id and snap.open_bills == 5 and snap.oldest_bill_date == date(2026, 9, 2)
        assert res.parties == 1 and res.unmatched == []


def test_bills_that_do_not_add_up_to_the_ledger_are_counted_not_hidden(client: TestClient) -> None:
    tid = _firm(client, "rg6@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        p = _party(s, tid, "Anand")
        record_master_guid(s, tid, c, "party", p, "p-1", name="Anand")
        apply_outstanding(s, c, [PartyBalance("Anand", D("1000"), guid="p-1")], TODAY, absent_is_zero=False)
        bills = [PendingBill(ref="B1", party="Anand", day=date(2026, 8, 1), due=None, closing=D("-600"))]
        res = apply_bill_dates(s, c, bills)
        assert res.off_by_ledger == 1  # 600 in bills against 1,000 in the ledger: an on-account amount
        assert s.scalar(select(PartyTallySnapshot.outstanding)) == D("1000.00")  # the ledger stays the balance


def test_unknown_parties_in_the_bills_are_listed(client: TestClient) -> None:
    tid = _firm(client, "rg7@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        res = apply_bill_dates(s, c, [PendingBill("B", "Nobody", date(2026, 8, 1), None, D("-5"))])
        assert res.unmatched == ["Nobody"] and res.parties == 0


def test_only_a_full_read_includes_the_bills_step() -> None:
    assert "bills" not in plan_steps(1, TODAY)
    assert plan_steps(1, TODAY, bills=True)[:4] == ["groups", "items", "ledgers", "bills"]


# ---------------------------------------------------------------- typed vs Tally's balance


def _differing(s, tid: str):
    c = add_company(s, tid, "GST")
    p = _party(s, tid, "Anand")
    p.opening_balance = D("300.00")
    record_master_guid(s, tid, c, "party", p, "p-1", name="Anand")
    apply_outstanding(s, c, [PartyBalance("Anand", D("1000"), guid="p-1")], TODAY, absent_is_zero=False)
    return c, p


def test_a_typed_balance_that_differs_is_listed(client: TestClient) -> None:
    tid = _firm(client, "rg8@x.example.com")
    with SessionLocal() as s:
        c, p = _differing(s, tid)
        assert differences(s, c) == [
            {"party_id": p.id, "party": "Anand", "ours": "300.00", "tally": "1000.00", "as_of": "2026-10-31"}
        ]


def test_taking_tallys_figure_sets_the_opening_balance_and_clears_the_difference(client: TestClient) -> None:
    tid = _firm(client, "rg9@x.example.com")
    with SessionLocal() as s:
        c, p = _differing(s, tid)
        settle_difference(s, c, p.id, "tally")
        s.refresh(p)
        assert p.opening_balance == D("1000.00") and p.opening_balance_as_of == TODAY
        assert differences(s, c) == []


def test_keeping_ours_hides_it_until_tallys_figure_moves(client: TestClient) -> None:
    tid = _firm(client, "rg10@x.example.com")
    with SessionLocal() as s:
        c, p = _differing(s, tid)
        settle_difference(s, c, p.id, "mine")
        s.refresh(p)
        assert p.opening_balance == D("300.00") and differences(s, c) == []
        # the same figure on the next read: still quiet, and not reported on the job either
        res = apply_outstanding(s, c, [PartyBalance("Anand", D("1000"), guid="p-1")], TODAY, absent_is_zero=False)
        assert res.differences == []
        # Tally's figure moves: it is worth another look
        res = apply_outstanding(s, c, [PartyBalance("Anand", D("1200"), guid="p-1")], TODAY, absent_is_zero=False)
        assert [d["tally"] for d in res.differences] == ["1200.00"] and len(differences(s, c)) == 1


def test_taking_tallys_figure_is_refused_once_the_party_has_a_ledger(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _firm(client, "rg11@x.example.com")
    with SessionLocal() as s:
        c, p = _differing(s, tid)
        monkeypatch.setattr(refill, "has_ledger_history", lambda session, party_id: True)
        with pytest.raises(DifferenceError):
            settle_difference(s, c, p.id, "tally")
        assert differences(s, c) == []  # and it is no longer offered: the balance is locked


def test_the_routes_list_and_settle_a_difference(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tok, tid = _register_firm(client, "rg12@x.example.com")
    _agent(tid)
    monkeypatch.setattr(tally_companies, "assert_tally_reachable", lambda session, company: None)
    with SessionLocal() as s:
        c, p = _differing(s, tid)
        s.commit()
        cid, pid = c.id, p.id
    base = f"/api/tally/companies/{cid}/refill"
    assert client.get(base, headers=_h(tok)).json()["differences"] == 1
    rows = client.get(base + "/differences", headers=_h(tok)).json()
    assert [r["party"] for r in rows] == ["Anand"]
    assert client.post(f"{base}/differences/{pid}", headers=_h(tok), json={"use": "bogus"}).status_code == 422
    assert client.post(f"{base}/differences/{pid}", headers=_h(tok), json={"use": "tally"}).status_code == 204
    assert client.get(base + "/differences", headers=_h(tok)).json() == []

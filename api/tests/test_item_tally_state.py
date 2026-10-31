"""S1.2b - an item's push state is kept per Tally company (services/tally/item_state.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item
from app.services.tally.companies import add_company
from app.services.tally.item_state import record_seen, record_sent, set_status, state_map
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_tally_links import _item


def _firm(client: TestClient, email: str) -> str:
    return _register_firm(client, email)[1]


def test_a_push_to_one_company_does_not_mark_the_other(client: TestClient) -> None:
    tid = _firm(client, "st1@x.example.com")
    with SessionLocal() as s:
        gst = add_company(s, tid, "GST")
        est = add_company(s, tid, "Estimate", kind="estimate")
        it = _item(s, tid)
        record_sent(s, gst, [it.id], {it.id: "100.00"}, datetime.now(UTC))
        s.commit()
        assert state_map(s, gst, [it.id])[it.id].status == "synced"
        assert it.id not in state_map(s, est, [it.id])
        set_status(s, est, [it.id], "queued")  # the estimate book is queued on its own
        s.commit()
        assert state_map(s, est, [it.id])[it.id].status == "queued"
        assert state_map(s, gst, [it.id])[it.id].status == "synced"


def test_a_price_update_keeps_a_synced_item_synced_and_only_first_sends_move_state(
    client: TestClient,
) -> None:
    tid = _firm(client, "st2@x.example.com")
    with SessionLocal() as s:
        c = add_company(s, tid, "GST")
        synced, fresh = _item(s, tid, "S"), _item(s, tid, "F")
        record_sent(s, c, [synced.id], {}, datetime.now(UTC))
        set_status(s, c, [synced.id, fresh.id], "queued")
        assert state_map(s, c, [synced.id])[synced.id].status == "synced"
        assert state_map(s, c, [fresh.id])[fresh.id].status == "queued"
        set_status(s, c, [synced.id, fresh.id], "error")
        assert state_map(s, c, [synced.id])[synced.id].status == "synced"
        assert state_map(s, c, [fresh.id])[fresh.id].status == "error"


def test_the_item_columns_show_the_default_company_only(client: TestClient) -> None:
    tid = _firm(client, "st3@x.example.com")
    with SessionLocal() as s:
        default = add_company(s, tid, "GST")
        other = add_company(s, tid, "Estimate", kind="estimate")
        a, b = _item(s, tid, "A"), _item(s, tid, "B")
        record_sent(s, default, [a.id], {a.id: "50.00"}, datetime.now(UTC))
        record_sent(s, other, [b.id], {b.id: "60.00"}, datetime.now(UTC))
        record_seen(s, default, {a.id: Decimal("0.00")})
        s.commit()
        rows = {i.name: i for i in s.scalars(select(Item).where(Item.tenant_id == tid))}
        assert rows["A"].tally_status == "synced" and rows["A"].tally_price == Decimal("50.00")
        assert rows["A"].tally_seen_price == Decimal("0.00")
        assert rows["B"].tally_status == "none" and rows["B"].tally_price is None


def test_an_item_push_is_blocked_when_the_company_is_not_the_one_we_linked(
    client: TestClient,
) -> None:
    from app.services.catalog import tally_items as ti

    tid = _firm(client, "st4@x.example.com")
    with SessionLocal() as s:
        from app.models import BackupShop

        shop = BackupShop(name="Shop", api_key_hash="k-st4", tenant_id=tid)
        s.add(shop)
        s.flush()
        c = add_company(s, tid, "GST", shop_id=shop.id)
        c.verify_state, c.verify_note = "different", "Tally has another company open."
        it = _item(s, tid)
        pf, _rows, _creates = ti.preflight(s, tid, [it])
        assert "company_different" in {x.code for x in pf.blockers}

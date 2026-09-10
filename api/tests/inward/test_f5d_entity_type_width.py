"""Repro: prod F5d approve 500 — Tally company + ledger_map present, all 12
lines match PRE-EXISTING items (unlike the fresh-tenant tests where approve
creates all 12). Approve must not 500."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from datetime import UTC, datetime

from app.db import SessionLocal
from app.domain.normalize import normalize_name
from app.models import BackupShop, Item, TallyCompany
from app.models._mixins import ItemType

from .conftest import auth, enable_inward_flag, register


@pytest.fixture
def seeded_hsn() -> None:  # some inward tests import this; harmless no-op here
    return None


def test_approve_with_tally_company_and_matched_items(
    client: TestClient, sugal_pdf_bytes: bytes
) -> None:
    tok = register(client, "repro-f5d@x.example.com")
    enable_inward_flag(client, tok)
    h = auth(tok)
    tid = client.get("/api/auth/me", headers=h).json()["tenant_id"]

    # Tally company + ledger_map exactly like prod
    with SessionLocal() as s:
        shop = BackupShop(
            tenant_id=tid,
            name="Repro",
            api_key_hash="x" * 64,
            is_active=True,
            last_checkin_at=datetime.now(UTC),   # agent "online"
            last_tally_status="connected",        # Tally "reachable" -> F5d hook RUNS
        )
        s.add(shop)
        s.flush()
        s.add(
            TallyCompany(
                tenant_id=tid,
                company_name="Fleek",
                shop_id=shop.id,
                transport="file",
                ledger_map={
                    "sales_ledger": "Cash",
                    "debtors_parent": "Sales Accounts",
                    "purchase_ledger": "ZZTEST Purchase",
                    "round_off_ledger": "ZZTEST Round Off P",
                    "cgst_ledger": "ZZTEST Plain",
                    "sgst_ledger": "ZZTEST Round Off",
                    "creditors_parent": "Sundry Creditors",
                },
            )
        )
        s.commit()

    # upload -> extract
    r = client.post(
        "/api/inward-bills",
        headers=h,
        files={"files": ("sugal.pdf", sugal_pdf_bytes, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    bill = r.json()[0]
    bid = bill["id"]
    descs = [ln["description"] for ln in bill["lines"]]

    # pre-create the 12 items so every line MATCHES (prod state)
    with SessionLocal() as s:
        for d in descs:
            s.add(
                Item(
                    tenant_id=tid,
                    name=d,
                    name_normalized=normalize_name(d, {}),
                    item_type=ItemType.bulk,
                    uom="Pcs",
                    hsn_code="21069092",
                )
            )
        s.commit()

    # re-extract so resolve_lines re-runs and matches them
    r = client.post(f"/api/inward-bills/{bid}/re-extract", headers=h)
    assert r.status_code == 200, r.text
    bill = client.get(f"/api/inward-bills/{bid}", headers=h).json()
    matched = sum(1 for ln in bill["lines"] if ln.get("matched_item_id"))
    print(f"\nlines matched to existing items: {matched}/{len(bill['lines'])}")
    print(f"approve_blockers: {bill['approve_blockers']}")

    client.patch(
        f"/api/inward-bills/{bid}", headers=h, json={"bill_date": "2026-09-02"}
    )

    r = client.post(f"/api/inward-bills/{bid}/approve", headers=h)
    print(f"approve: {r.status_code} {r.text[:1500]}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    # the F5d hook ran (agent online + Tally connected) and enqueued a
    # push_purchase job — entity_type 'inward_bill' is 11 chars, which
    # overflowed VARCHAR(10) on Postgres before 0031 and poisoned approve.
    from app.models import TallySyncJob

    with SessionLocal() as s:
        job = s.scalar(
            select(TallySyncJob).where(
                TallySyncJob.kind == "push_purchase",
                TallySyncJob.entity_type == "inward_bill",
                TallySyncJob.entity_id == bid,
            )
        )
        assert job is not None, "F5d hook did not enqueue a push_purchase job"
        assert job.status == "queued"
        assert len("inward_bill") == 11  # the value that must fit the column

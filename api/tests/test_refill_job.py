"""The refill as a chain of agent jobs (services/tally/refill_job.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BackupShop, ItemTallyState, PartyItemPrice, TallySyncJob
from app.services.tally import refill_job
from app.services.tally.companies import add_company
from app.services.tally.refill_job import (
    apply_refill_result,
    is_refill_job,
    plan_steps,
    start_refill,
)
from app.services.tally.voucher_capture import (
    build_groups_request,
    build_ledgers_request,
    build_stock_items_request,
    is_export_request,
    process_capture_result,
)
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_refill import _sales_xml
from tests.test_tally_links import _item, _party

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 31)


def _company(s, tid: str):
    shop = BackupShop(name="shop", api_key_hash="h", tenant_id=tid, is_active=True)
    s.add(shop)
    s.flush()
    return add_company(s, tid, "GST", shop_id=shop.id)


def _finish(s, job: TallySyncJob, body: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refill_job, "get_object", lambda key, *a, **k: body)
    process_capture_result(s, job, ok=True, r2_key="k/" + job.id, meta_json=None, agent_error=None)
    apply_refill_result(s, job, today=TODAY)


def _next(s, after: TallySyncJob) -> TallySyncJob | None:
    return s.scalar(
        select(TallySyncJob)
        .where(TallySyncJob.id != after.id, TallySyncJob.status == "queued")
        .order_by(TallySyncJob.created_at.desc())
    )


def test_the_plan_is_stock_then_bills_then_newest_month_first() -> None:
    assert plan_steps(3, date(2026, 10, 31)) == [
        "groups",
        "items",
        "ledgers",
        "sales:2026-10",
        "sales:2026-09",
        "sales:2026-08",
    ]
    assert plan_steps(2, date(2026, 1, 15))[-2:] == ["sales:2026-01", "sales:2025-12"]
    assert len(plan_steps(99, TODAY)) == 3 + 12  # capped at a year


def test_the_light_requests_are_read_only_exports_naming_the_company_and_the_fields() -> None:
    led = build_ledgers_request("ACME & Sons")
    assert is_export_request(build_groups_request("ACME")) and "<TYPE>Group</TYPE>" in build_groups_request("ACME")
    itm = build_stock_items_request("ACME & Sons", date(2026, 10, 31))
    for xml in (led, itm):
        assert is_export_request(xml) and "ACME &amp; Sons" in xml
    assert "Guid" in led and "ClosingBalance" in led and "<TYPE>Ledger</TYPE>" in led
    assert "Guid" in itm and "ClosingValue" in itm and "<SVTODATE>20261031</SVTODATE>" in itm


def test_a_refresh_runs_the_chain_one_job_at_a_time_and_applies_each_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "rj1@x.example.com")[1]
    ledgers = (FIX / "tally_ledgers_dev_sample.xml").read_bytes()
    bills = (FIX / "tally_bills_receivable_dev_sample.xml").read_bytes()
    with SessionLocal() as s:
        c = _company(s, tid)
        _item(s, tid, "Monin Blueberry Syrup 700Mlx6")
        _party(s, tid, "ZZTEST Customer")
        _party(s, tid, "Anand Metals")
        _item(s, tid, "SS Thali")

        zero = start_refill(s, c, months=1, today=TODAY)
        assert is_refill_job(zero) and zero.counts["step"] == "groups"
        assert zero.counts["steps_left"] == ["items", "ledgers", "bills", "sales:2026-10"]
        _finish(s, zero, (FIX / "tally_groups_dev_sample.xml").read_bytes(), monkeypatch)
        assert zero.counts["refill"]["groups"]["groups"] == 28
        first = _next(s, zero)
        assert first is not None and first.counts["step"] == "items"
        assert first.counts["groups_map"]["Sundry Debtors"] == "Current Assets"  # carried down the chain

        _finish(s, first, (FIX / "tally_stock_items_dev_sample.xml").read_bytes(), monkeypatch)
        assert first.status == "ok" and first.counts["refill"]["items"]["matched"] == 1
        second = _next(s, first)
        assert second is not None and second.counts["step"] == "ledgers"
        assert second.counts["groups_map"]

        _finish(s, second, ledgers, monkeypatch)
        assert second.counts["refill"]["ledgers"]["matched"] == 1
        bills_job = _next(s, second)
        assert bills_job is not None and bills_job.counts["step"] == "bills"  # a full read adds the bill dates
        _finish(s, bills_job, bills, monkeypatch)
        assert bills_job.counts["refill"]["bills"]["parties"] == 1
        third = _next(s, bills_job)
        assert third is not None and third.counts["step"] == "sales:2026-10"
        assert third.counts["steps_left"] == []

        sales = _sales_xml([("g1", "20261005", "9", "Anand Metals", "SS Thali", "110.00")])
        _finish(s, third, sales, monkeypatch)
        s.commit()
        assert third.counts["refill"]["sales:2026-10"]["prices"] == 1
        assert s.scalar(select(PartyItemPrice.rate)) == Decimal("110.00")
        assert s.scalar(select(ItemTallyState.stock_qty)) is not None
        assert _next(s, third) is None  # the chain is finished


def test_a_repeated_callback_does_not_apply_or_queue_twice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "rj2@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = start_refill(s, c, months=1, today=TODAY)
        body = (FIX / "tally_stock_items_dev_sample.xml").read_bytes()
        _finish(s, job, body, monkeypatch)
        apply_refill_result(s, job, today=TODAY)  # the agent retried its callback
        queued = s.scalars(select(TallySyncJob).where(TallySyncJob.status == "queued")).all()
        assert len(queued) == 1


def test_an_unreadable_file_ends_the_chain_with_a_plain_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "rj3@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = start_refill(s, c, months=1, today=TODAY)

        def boom(key, *a, **k):
            raise ValueError("R2 object is 78428289 bytes, over the 64 MB limit")

        monkeypatch.setattr(refill_job, "get_object", boom)
        process_capture_result(s, job, ok=True, r2_key="k", meta_json=None, agent_error=None)
        apply_refill_result(s, job, today=TODAY)
        assert job.status == "error" and "64 MB" in job.error
        assert _next(s, job) is None


def test_a_second_refresh_cannot_start_while_one_is_running(client: TestClient) -> None:
    from fastapi import HTTPException

    tid = _register_firm(client, "rj4@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        start_refill(s, c, today=TODAY)
        with pytest.raises(HTTPException) as err:
            start_refill(s, c, today=TODAY)
        assert err.value.status_code == 409

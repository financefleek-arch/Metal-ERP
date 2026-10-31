"""Reading only what changed since last time, and the daily refresh (services/tally/refill_job.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BackupShop, TallyCompany, TallySyncJob
from app.services.tally import refill_job
from app.services.tally.companies import add_company
from app.services.tally.refill_job import (
    FULL_EVERY_DAYS,
    apply_refill_result,
    choose_mode,
    due_for_auto_refresh,
    run_auto_refresh,
    start_refill,
)
from app.services.tally.voucher_capture import (
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

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 31)
NOON_IST = datetime(2026, 10, 31, 6, 30, tzinfo=UTC)  # 12:00 in India


def _company(s, tid: str, *, online: bool = True, auto: bool = False) -> TallyCompany:
    shop = BackupShop(
        name="shop",
        api_key_hash="h",
        tenant_id=tid,
        is_active=True,
        last_checkin_at=datetime.now(UTC) if online else None,
    )
    s.add(shop)
    s.flush()
    c = add_company(s, tid, "GST", shop_id=shop.id)
    c.auto_refresh = auto
    s.flush()
    return c


def _run_chain(s, first: TallySyncJob, files: dict[str, bytes], monkeypatch: pytest.MonkeyPatch) -> None:
    job = first
    while job is not None:
        body = files.get(job.counts["step"].split(":")[0], b"<ENVELOPE/>")
        monkeypatch.setattr(refill_job, "get_object", lambda key, *a, **k: body)
        process_capture_result(s, job, ok=True, r2_key="k/" + job.id, meta_json=None, agent_error=None)
        apply_refill_result(s, job, today=TODAY)
        job = s.scalar(
            select(TallySyncJob).where(TallySyncJob.status == "queued").order_by(TallySyncJob.created_at)
        )


FILES = {
    "groups": (FIX / "tally_groups_dev_sample.xml").read_bytes(),
    "items": (FIX / "tally_stock_items_dev_sample.xml").read_bytes(),
    "ledgers": (FIX / "tally_ledgers_dev_sample.xml").read_bytes(),
}


# ---------------------------------------------------------------------------- the requests


def test_a_changes_request_filters_on_the_alter_id_and_is_still_a_read_only_export() -> None:
    full = build_ledgers_request("ACME")
    assert "MEAlter" not in full and "AlterID" in full
    inc = build_ledgers_request("ACME", alter_after=217)
    assert is_export_request(inc)
    assert "<FILTER>MEAlter</FILTER>" in inc and "$AlterID &gt; 217" in inc
    assert "$AlterID &gt; 1540" in build_stock_items_request("ACME", alter_after=1540)
    assert inc.count("</TDLMESSAGE>") == 1  # the formula sits inside the TDL message, well formed


def test_the_alter_id_is_read_from_the_live_capture() -> None:
    from app.services.tally.light_masters import parse_ledgers, parse_stock_items

    assert max(r.alter_id for r in parse_ledgers(FILES["ledgers"])) == 248
    assert all(r.alter_id is not None for r in parse_stock_items(FILES["items"]))


# ------------------------------------------------------------------------- choosing the mode


def test_the_first_read_and_a_stale_one_are_full_a_recent_one_is_changes(client: TestClient) -> None:
    tid = _register_firm(client, "ri1@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        assert choose_mode(c) == "full"  # nothing read yet
        c.items_alter_id, c.ledgers_alter_id = 10, 20
        assert choose_mode(c) == "full"  # markers but no complete read on record
        c.full_refill_at = datetime.now(UTC) - timedelta(days=1)
        assert choose_mode(c) == "changes"
        c.full_refill_at = datetime.now(UTC) - timedelta(days=FULL_EVERY_DAYS + 1)
        assert choose_mode(c) == "full"  # deletions are not changes: a full read comes round weekly
        c.full_refill_at = datetime.now(UTC)
        c.items_alter_id = None
        assert choose_mode(c) == "full"


# ------------------------------------------------------------------------------ the chain


def test_a_full_chain_sets_the_markers_and_the_next_refresh_asks_only_for_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "ri2@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        first = start_refill(s, c, months=1, today=TODAY)
        assert first.counts["mode"] == "full"
        _run_chain(s, first, FILES, monkeypatch)
        s.commit()
        s.refresh(c)
        assert c.ledgers_alter_id == 248
        assert c.items_alter_id and c.items_alter_id > 1000
        assert c.full_refill_at is not None  # the whole chain finished

        second = start_refill(s, c, today=TODAY)
        assert second.counts["mode"] == "changes" and second.counts["step"] == "groups"
        assert second.counts["window_days"] == 15  # the size the first read finished with, tried again
        assert [w for w in second.counts["steps_left"] if w.startswith("sales:")]
        # items and ledgers ask for what is above the markers; groups never filter
        items_job_xml = refill_job._request_for("items", c, TODAY, "changes")
        assert f"$AlterID &gt; {c.items_alter_id}" in items_job_xml
        assert "MEAlter" not in refill_job._request_for("groups", c, TODAY, "changes")


def test_a_failed_chain_does_not_claim_a_full_read_and_a_marker_never_moves_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "ri3@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        c.ledgers_alter_id = 900  # ahead of anything in the file
        first = start_refill(s, c, months=1, today=TODAY)
        job = first
        files = dict(FILES)
        # groups and items succeed, then the ledger file cannot be read
        for step in ("groups", "items"):
            monkeypatch.setattr(refill_job, "get_object", lambda key, *a, _b=files[step], **k: _b)
            process_capture_result(s, job, ok=True, r2_key="k", meta_json=None, agent_error=None)
            apply_refill_result(s, job, today=TODAY)
            job = s.scalar(select(TallySyncJob).where(TallySyncJob.status == "queued"))

        def boom(key, *a, **k):
            raise ValueError("too big")

        monkeypatch.setattr(refill_job, "get_object", boom)
        process_capture_result(s, job, ok=True, r2_key="k", meta_json=None, agent_error=None)
        apply_refill_result(s, job, today=TODAY)
        assert job.status == "error" and c.full_refill_at is None
        assert c.items_alter_id is not None  # the step that did succeed keeps its marker
        assert c.ledgers_alter_id == 900


def test_forcing_a_full_read_ignores_the_markers(client: TestClient) -> None:
    tid = _register_firm(client, "ri4@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        c.items_alter_id = c.ledgers_alter_id = 5
        c.full_refill_at = datetime.now(UTC)
        job = start_refill(s, c, full=True, today=TODAY)
        assert job.counts["mode"] == "full" and len(job.counts["steps_left"]) == 3 + 1 + 6


# ------------------------------------------------------------------------- the daily refresh


def test_only_an_opted_in_company_with_an_online_agent_and_no_recent_read_is_due(
    client: TestClient,
) -> None:
    tid = _register_firm(client, "ri5@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid, auto=False)
        assert not due_for_auto_refresh(s, c, NOON_IST)  # not opted in
        c.auto_refresh = True
        assert due_for_auto_refresh(s, c, NOON_IST)
        s.get(BackupShop, c.shop_id).last_checkin_at = datetime.now(UTC) - timedelta(hours=1)
        assert not due_for_auto_refresh(s, c, datetime.now(UTC))  # the agent is offline


def test_run_acts_in_working_hours_once_a_day_and_skips_the_night(client: TestClient) -> None:
    tid = _register_firm(client, "ri6@x.example.com")[1]
    with SessionLocal() as s:
        _company(s, tid, auto=True)
        s.commit()
    night = datetime(2026, 10, 31, 21, 0, tzinfo=UTC)  # 02:30 in India
    assert run_auto_refresh(night) == 0
    assert run_auto_refresh(NOON_IST) == 1
    assert run_auto_refresh(NOON_IST) == 0  # a read is already queued: not started twice
    with SessionLocal() as s:
        jobs = s.scalars(select(TallySyncJob).where(TallySyncJob.kind == "pull_vouchers")).all()
        assert len(jobs) == 1 and jobs[0].counts["step"] == "groups"


def test_a_company_that_just_failed_is_not_retried_until_tomorrow(client: TestClient) -> None:
    tid = _register_firm(client, "ri7@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid, auto=True)
        job = start_refill(s, c, today=TODAY)
        job.status, job.error = "error", "Tally was closed"
        s.commit()
    assert run_auto_refresh(NOON_IST) == 0

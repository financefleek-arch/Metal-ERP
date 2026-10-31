"""A sales window Tally is too slow for is split and read again, and the owner is told in plain words
(services/tally/refill_job.py)."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BackupShop, TallyCompany, TallySyncJob
from app.routers import tally_companies
from app.services.tally import refill_job
from app.services.tally.companies import add_company
from app.services.tally.refill_job import (
    MAX_TIMEOUTS,
    apply_refill_result,
    split_window,
    start_refill,
    window_of,
)
from app.services.tally.voucher_capture import process_capture_result
from tests.test_invoice_tally_push_integration import (  # noqa: F401
    _register_firm,
    _stub_installer_build,
    client,
)
from tests.test_owner_companies import _h

TODAY = date(2026, 10, 31)
SLOW = "Tally did not answer in time. Try a shorter period."


def _company(s, tid: str) -> TallyCompany:
    shop = BackupShop(name="shop", api_key_hash="h", tenant_id=tid, is_active=True)
    s.add(shop)
    s.flush()
    return add_company(s, tid, "GST", shop_id=shop.id)


def _to_the_sales(s, c: TallyCompany, monkeypatch: pytest.MonkeyPatch) -> TallySyncJob:
    """A chain with its masters steps done, positioned at the first sales window."""
    job = start_refill(s, c, months=1, full=True, today=TODAY)
    monkeypatch.setattr(refill_job, "get_object", lambda key, *a, **k: b"<ENVELOPE/>")
    while not job.counts["step"].startswith("sales:"):
        process_capture_result(s, job, ok=True, r2_key="k/" + job.id, meta_json=None, agent_error=None)
        apply_refill_result(s, job, today=TODAY)
        job = s.scalar(select(TallySyncJob).where(TallySyncJob.status == "queued"))
    return job


def _fail(s, job: TallySyncJob, why: str = SLOW) -> None:
    process_capture_result(s, job, ok=False, r2_key=None, meta_json=None, agent_error=why)
    apply_refill_result(s, job, today=TODAY)


def _queued(s) -> list[TallySyncJob]:
    return list(s.scalars(select(TallySyncJob).where(TallySyncJob.status == "queued")))


def test_a_window_reads_as_days_and_the_old_month_form_still_works() -> None:
    assert window_of("sales:2026-10-16..2026-10-31", TODAY) == (date(2026, 10, 16), date(2026, 10, 31))
    assert window_of("sales:2026-10-16..2026-10-31", date(2026, 10, 20)) == (date(2026, 10, 16), date(2026, 10, 20))
    assert window_of("sales:2026-09", TODAY) == (date(2026, 9, 1), date(2026, 9, 30))  # queued before windows
    newer, older = split_window(date(2026, 10, 1), date(2026, 10, 15))
    assert (older, newer) == ((date(2026, 10, 1), date(2026, 10, 8)), (date(2026, 10, 9), date(2026, 10, 15)))


def test_a_slow_window_is_split_in_two_newest_first_and_the_chain_goes_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to1@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        assert job.counts["step"] == "sales:2026-10-16..2026-10-31"
        _fail(s, job)
        nxt = _queued(s)
        assert len(nxt) == 1 and nxt[0].counts["step"] == "sales:2026-10-24..2026-10-31"
        # everything still to come is cut to the size that has just been shown too big, not left to time out
        assert nxt[0].counts["steps_left"] == [
            "sales:2026-10-16..2026-10-23",
            "sales:2026-10-08..2026-10-15",
            "sales:2026-10-01..2026-10-07",
        ]
        assert nxt[0].counts["timeouts"] == 1 and nxt[0].counts["window_days"] == 8
        assert "Tally was slow on sales for 16 Oct to 31 Oct 2026" in nxt[0].counts["notes"][0]
        assert job.counts["handled"] is True and "did not answer" in job.error  # the failure stays on record


def test_a_repeated_failure_callback_does_not_split_twice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to2@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        _fail(s, job)
        apply_refill_result(s, job, today=TODAY)  # the agent reported the failure again
        assert len(_queued(s)) == 1


def test_a_few_days_that_still_time_out_are_skipped_and_the_next_window_is_tried(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to3@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        job.counts = {**job.counts, "step": "sales:2026-10-31..2026-10-31"}  # one day: nothing left to split
        _fail(s, job)
        nxt = _queued(s)
        assert len(nxt) == 1 and nxt[0].counts["step"] == "sales:2026-10-01..2026-10-15"
        assert "skipped" in nxt[0].counts["notes"][0]


def test_repeated_timeouts_stop_the_sales_but_keep_everything_else(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to4@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        job.counts = {**job.counts, "timeouts": MAX_TIMEOUTS - 1}
        _fail(s, job)
        assert _queued(s) == []  # nothing more is queued
        note = job.counts["notes"][-1]
        assert "Stock, balances and bill dates are in" in note and "so the remaining sales were left" in note
        assert c.full_refill_at is not None  # the masters side of the read did finish


def test_any_other_failure_ends_the_chain_without_a_split(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to5@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        _fail(s, job, "Tally answered with an error (500).")
        assert _queued(s) == [] and "handled" not in job.counts
        assert job.status == "error" and "500" in job.error


def test_the_masters_part_counts_as_a_complete_read_even_if_the_sales_never_finish(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to6@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        assert c.full_refill_at is None
        _to_the_sales(s, c, monkeypatch)
        assert c.full_refill_at is not None  # set when the bills step finished, before any sales


def test_the_status_route_reports_the_notes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tok, tid = _register_firm(client, "to7@x.example.com")
    monkeypatch.setattr(tally_companies, "assert_tally_reachable", lambda session, company: None)
    with SessionLocal() as s:
        c = _company(s, tid)
        cid = c.id
        job = _to_the_sales(s, c, monkeypatch)
        _fail(s, job)
        for earlier in s.scalars(select(TallySyncJob).where(TallySyncJob.status != "queued")):
            earlier.created_at = earlier.created_at - timedelta(minutes=2)  # jobs made in one second would tie
        s.commit()
    st = client.get(f"/api/tally/companies/{cid}/refill", headers=_h(tok)).json()
    assert st["running"] is True and st["step"] == "sales:2026-10-24..2026-10-31"
    assert len(st["notes"]) == 1 and "8 days at a time" in st["notes"][0]


def _ok_with(s, job: TallySyncJob, ms: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refill_job, "get_object", lambda key, *a, **k: b"<ENVELOPE/>")
    process_capture_result(
        s, job, ok=True, r2_key="k/" + job.id, meta_json=f'{{"ms": {ms}, "bytes": 10}}', agent_error=None
    )
    apply_refill_result(s, job, today=TODAY)


def test_a_quick_answer_lets_the_next_windows_grow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _register_firm(client, "to8@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        job.counts = {
            **job.counts,
            "step": "sales:2026-10-29..2026-10-31",
            "steps_left": ["sales:2026-10-01..2026-10-28"],
        }
        _ok_with(s, job, 3000, monkeypatch)  # 3 days answered in 3 seconds
        (nxt,) = _queued(s)
        assert nxt.counts["window_days"] == 6 and nxt.counts["step"] == "sales:2026-10-23..2026-10-28"
        assert job.counts["window_days"] == 6


def test_a_slow_answer_does_not_let_them_grow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _register_firm(client, "to9@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        job.counts = {**job.counts, "step": "sales:2026-10-29..2026-10-31", "steps_left": ["sales:2026-10-01..2026-10-28"]}
        _ok_with(s, job, 80_000, monkeypatch)  # close to the limit: keep the size
        (nxt,) = _queued(s)
        assert nxt.counts["step"] == "sales:2026-10-01..2026-10-28" and nxt.counts["window_days"] == 3


def test_the_size_that_worked_is_the_next_reads_starting_size(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _register_firm(client, "to10@x.example.com")[1]
    with SessionLocal() as s:
        c = _company(s, tid)
        job = _to_the_sales(s, c, monkeypatch)
        _fail(s, job)  # 16 days too big: 8 now, and that is remembered
        (second,) = _queued(s)
        _ok_with(s, second, 90_000, monkeypatch)  # 8 days answered, but slowly: stays 8
        for left in _queued(s):
            left.status = "ok"  # the rest of that read is over
        s.flush()
        assert refill_job.learned_window(s, c) == 8
        nxt = start_refill(s, c, months=1, full=True, today=TODAY)
        assert nxt.counts["window_days"] == 8
        windows = [nxt.counts["step"], *nxt.counts["steps_left"]]
        assert [w for w in windows if w.startswith("sales:")][0] == "sales:2026-10-24..2026-10-31"
        assert len([w for w in windows if w.startswith("sales:")]) == 4  # 31 days in windows of 8


def test_nothing_learned_yet_means_half_months() -> None:
    steps = refill_job.plan_steps(1, TODAY)
    assert steps[-2:] == ["sales:2026-10-16..2026-10-31", "sales:2026-10-01..2026-10-15"]
    sized = [w for w in refill_job.plan_steps(1, TODAY, window_days=10) if w.startswith("sales:")]
    assert sized == [
        "sales:2026-10-22..2026-10-31",
        "sales:2026-10-12..2026-10-21",
        "sales:2026-10-02..2026-10-11",
        "sales:2026-10-01..2026-10-01",
    ]

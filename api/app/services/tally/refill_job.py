"""Run the refill (`refill.py`) as a chain of read-only agent jobs.

One refresh is a short chain, one job at a time (a firm may have only one capture in flight):

    groups  ->  items  ->  ledgers  ->  sales:<this month>  ->  sales:<last month>  ->  ...

The item and ledger queries are one small file each (about 500 bytes a row, keyed by Tally's own id). Sales vouchers are pulled a month at a time so
no single file gets near the download limit that broke the first masters pull (78 MB, 2026-10-26), and the
newest month runs first so the latest prices are in before the older months are read.

Each job is a `pull_vouchers` capture (the agent action that exists for raw capture: it sends one read-only
Export request and parks the XML in R2), labelled `refill-<step>`. When the agent reports a job done the
router calls `apply_refill_result`, which parses the file, applies it, records what happened on the job and
queues the next step. A failed step ends the chain; whatever earlier steps wrote stays, because every step
is a complete, independent overwrite.

Nothing here is a new agent action, so it needs no agent release beyond the one that carries
`pull_vouchers` (not in 1.0.2).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backup_storage import R2NotConfigured, get_object
from app.db import SessionLocal
from app.models import BackupShop, TallyCompany, TallySyncJob
from app.services.tally.agent_health import agent_online
from app.services.tally.jobs import complete_job_error
from app.services.tally.light_masters import (
    parse_groups,
    parse_ledgers,
    parse_stock_items,
    parse_voucher_types,
)
from app.services.tally.refill import (
    apply_bill_dates,
    apply_outstanding,
    apply_sales,
    apply_stock,
    balances_from_ledgers,
)
from app.services.tally.voucher_capture import (
    assert_no_capture_in_flight,
    build_bills_receivable_request,
    build_groups_request,
    build_ledgers_request,
    build_stock_items_request,
    build_voucher_types_request,
    build_vouchers_request,
    enqueue_voucher_capture,
)
from app.services.tally.voucher_xml import parse_bills_receivable, parse_vouchers
from tools.tally_import.parser import TallyGroup

log = logging.getLogger("tally.refill")

LABEL_PREFIX = "refill-"
DEFAULT_MONTHS = 3  # a full read goes back this far for sales
CHANGES_MONTHS = 2  # a read of changes re-reads this month and the last: a late or backdated bill lands in one
MAX_MONTHS = 12
FULL_EVERY_DAYS = 7  # a deleted record is not a change, so a full read runs at least this often
AUTO_EVERY_HOURS = 20
AUTO_FROM_HOUR_IST = 8  # a shop's PC and Tally are on in working hours, not overnight
AUTO_UNTIL_HOUR_IST = 20
_IST = timezone(timedelta(hours=5, minutes=30))
_LIST_CAP = 50  # unmatched names kept on the job; the count is always exact
# The agent gives Tally about 100 seconds to answer one request (an agent release is needed to change
# that). Sales are read in windows of days, and the size is tuned as the read goes: a window Tally cannot
# answer in time is halved (and every window still to come with it), one it answers quickly lets the next
# ones grow, and the size that worked is remembered for the next read. A single day that still times out is
# skipped; after two timeouts in one read the rest of the sales are left, with a plain note.
TIMEOUT_TEXT = "did not answer in time"
MAX_TIMEOUTS = 2  # the agent gives up after ~105 s but Tally does not cancel what it was asked, so every
# abandoned request may still be running inside the shop's Tally: stop sooner rather than pile more on it
SALES_FAST_MS = 25_000  # answered in under this: the next windows may be twice as long
SALES_MAX_WINDOW = 15  # days


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _prev_month(d: date) -> date:
    return _month_start(_month_start(d) - timedelta(days=1))


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def sales_step(start: date, end: date) -> str:
    return f"sales:{start:%Y-%m-%d}..{end:%Y-%m-%d}"


def window_of(step: str, today: date) -> tuple[date, date]:
    """The days a sales step covers, never past today. Reads both `sales:2026-10-16..2026-10-31` and the
    older whole-month form `sales:2026-10`, which a read queued before the windows may still carry."""
    body = step.split(":", 1)[1]
    if ".." in body:
        a, b = body.split("..")
        start, end = date.fromisoformat(a), date.fromisoformat(b)
    else:
        start = datetime.strptime(body, "%Y-%m").date()
        end = _month_end(start)
    return start, min(end, today)


def split_window(start: date, end: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """Two halves, the newer first (the latest prices matter most)."""
    mid = start + timedelta(days=(end - start).days // 2)
    return (mid + timedelta(days=1), end), (start, mid)


def _chunks(start: date, end: date, size: int) -> list[tuple[date, date]]:
    """[start, end] cut into pieces of at most `size` days, newest first."""
    out: list[tuple[date, date]] = []
    hi = end
    while hi >= start:
        lo = max(start, hi - timedelta(days=size - 1))
        out.append((lo, hi))
        hi = lo - timedelta(days=1)
    return out


def _resize(steps: list[str], size: int, today: date) -> list[str]:
    """The sales windows among `steps` re-cut to at most `size` days each; other steps are left as they are."""
    out: list[str] = []
    for step in steps:
        if not step.startswith("sales:"):
            out.append(step)
            continue
        start, end = window_of(step, today)
        if start > end:
            continue
        out += [sales_step(a, b) for a, b in _chunks(start, end, size)]
    return out


def plan_steps(
    months: int, today: date, *, bills: bool = False, window_days: int | None = None
) -> list[str]:
    """`bills` adds the pending-bills read (dates for ageing); a full read does, a read of changes does not.
    Sales go newest first in windows of `window_days` (what this shop's Tally was last able to answer), or
    half a month when that is not known yet: a request a shop's Tally can answer well inside the agent's
    wait, where a whole month of a busy shop may not."""
    months = max(1, min(months, MAX_MONTHS))
    # the voucher types come just before the sales: they say which of a shop's own document types are sales
    steps = ["groups", "items", "ledgers"] + (["bills"] if bills else []) + ["vtypes"]
    first = _month_start(today)
    for _ in range(months - 1):
        first = _prev_month(first)
    if window_days:
        steps += [sales_step(a, b) for a, b in _chunks(first, today, max(1, window_days))]
        return steps
    month = _month_start(today)
    for _ in range(months):
        last = _month_end(month)
        for start, end in ((month.replace(day=16), last), (month, month.replace(day=15))):
            if start <= today:
                steps.append(sales_step(start, end))
        month = _prev_month(month)
    return steps


def learned_window(session: Session, company: TallyCompany) -> int | None:
    """The window size the last read of this company's sales finished with: what its Tally can answer."""
    for job in session.scalars(
        select(TallySyncJob)
        .where(TallySyncJob.company_id == company.id, TallySyncJob.kind == "pull_vouchers")
        .order_by(TallySyncJob.created_at.desc())
        .limit(40)
    ):
        size = (job.counts or {}).get("window_days")
        if isinstance(size, int) and size > 0:
            return min(size, SALES_MAX_WINDOW)
    return None


def is_refill_job(job: TallySyncJob) -> bool:
    return str((job.counts or {}).get("label", "")).startswith(LABEL_PREFIX)


def choose_mode(company: TallyCompany, now: datetime | None = None) -> str:
    """"changes" only when we hold the markers and the last complete read is recent; else "full"."""
    now = now or datetime.now(UTC)
    last = company.full_refill_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if (
        company.items_alter_id is None
        or company.ledgers_alter_id is None
        or last is None
        or now - last > timedelta(days=FULL_EVERY_DAYS)
    ):
        return "full"
    return "changes"


def _request_for(step: str, company: TallyCompany, today: date, mode: str = "full") -> str:
    changes = mode == "changes"
    if step == "groups":
        return build_groups_request(company.company_name, today)
    if step == "items":
        return build_stock_items_request(
            company.company_name, today, company.items_alter_id if changes else None
        )
    if step == "bills":
        return build_bills_receivable_request(company.company_name, today)
    if step == "vtypes":
        return build_voucher_types_request(company.company_name, today)
    if step == "ledgers":
        return build_ledgers_request(
            company.company_name, today, company.ledgers_alter_id if changes else None
        )
    start, end = window_of(step, today)
    return build_vouchers_request(company.company_name, start, end)


def _enqueue(
    session: Session,
    company: TallyCompany,
    steps: list[str],
    done: dict,
    today: date,
    carry: dict | None = None,
    mode: str = "full",
) -> TallySyncJob:
    """`carry`: what travels down the chain (the group tree, notes for the owner, the timeout count)."""
    step, rest = steps[0], steps[1:]
    job = enqueue_voucher_capture(
        session,
        company,
        label=f"{LABEL_PREFIX}{step.replace(':', '-')}",
        request_xml=_request_for(step, company, today, mode),
    )
    job.counts = {
        **(job.counts or {}),
        "step": step,
        "steps_left": rest,
        "refill": done,
        "groups_map": (carry or {}).get("groups_map") or {},
        "voucher_types": (carry or {}).get("voucher_types") or {},
        "notes": list((carry or {}).get("notes") or []),
        "timeouts": int((carry or {}).get("timeouts") or 0),
        "window_days": (carry or {}).get("window_days"),
        "mode": mode,
    }
    session.flush()
    return job


def start_refill(
    session: Session,
    company: TallyCompany,
    *,
    months: int | None = None,
    full: bool = False,
    today: date | None = None,
) -> TallySyncJob:
    """Queue the first step of a refresh. The caller has checked the agent is reachable.

    Unless `full` is asked for, the read is incremental whenever the company holds its change markers and
    had a complete read within a week: only ledgers and items changed since, and the last two months of
    sales. The first read, and one a week, is complete."""
    assert_no_capture_in_flight(session, company.tenant_id)
    today = today or datetime.now(UTC).date()
    mode = "full" if full else choose_mode(company)
    size = learned_window(session, company)
    if months is None:
        months = DEFAULT_MONTHS if mode == "full" else CHANGES_MONTHS
        if mode == "changes" and size is not None and size < 7:
            months = 1  # a slow Tally: a month of changes is enough to catch up each day, a second is weekly
    steps = plan_steps(months, today, bills=mode == "full", window_days=size)
    return _enqueue(session, company, steps, {}, today, {"window_days": size}, mode)


def due_for_auto_refresh(session: Session, company: TallyCompany, now: datetime) -> bool:
    if not company.auto_refresh or company.status != "active" or company.shop_id is None:
        return False
    shop = session.get(BackupShop, company.shop_id)
    if shop is None or not agent_online(shop):
        return False
    latest = session.scalar(
        select(TallySyncJob.created_at)
        .where(TallySyncJob.company_id == company.id, TallySyncJob.kind == "pull_vouchers")
        .order_by(TallySyncJob.created_at.desc())
        .limit(1)
    )
    # the latest capture of any kind counts: a chain still running, or one that just failed, is not retried
    # until the next day, so a shop with Tally closed is not hammered
    if latest is not None:
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        if now - latest < timedelta(hours=AUTO_EVERY_HOURS):
            return False
    return True


def run_auto_refresh(now: datetime | None = None) -> int:
    """For each company that opted in and is due, queue a refresh. Idempotent: a company with a recent
    capture, an offline agent or a read in flight is skipped, so repeating this or running two copies is
    harmless. Called hourly; acts only in working hours (India time)."""
    now = now or datetime.now(UTC)
    if not AUTO_FROM_HOUR_IST <= now.astimezone(_IST).hour < AUTO_UNTIL_HOUR_IST:
        return 0
    started = 0
    with SessionLocal() as session:
        for company in session.scalars(select(TallyCompany).where(TallyCompany.auto_refresh.is_(True))):
            try:
                if not due_for_auto_refresh(session, company, now):
                    continue
                start_refill(session, company, today=now.astimezone(_IST).date())
                session.commit()
                started += 1
            except Exception:  # noqa: BLE001 - one shop's trouble must not stop the others
                session.rollback()
                log.exception("auto refresh: company %s skipped", company.id)
    return started


def _advance(current: int | None, seen: list[int | None]) -> int | None:
    """The marker only ever moves up, and only on what Tally actually sent."""
    values = [v for v in seen if v is not None]
    if not values:
        return current
    return max([current or 0, *values])


@dataclass
class _Types:
    """The voucher types read, nothing applied."""

    voucher_types: int = 0


@dataclass
class _Counted:
    """The result of a step that only reads (the group list): what it found, nothing applied."""

    groups: int = 0


def _summary(result) -> dict:  # type: ignore[no-untyped-def]
    out = asdict(result)
    for key, value in list(out.items()):
        if isinstance(value, list):
            out[f"{key}_count"] = len(value)
            out[key] = value[:_LIST_CAP]
    return out


def apply_refill_result(session: Session, job: TallySyncJob, today: date | None = None) -> None:
    """Called when a refill capture finished: if it failed on a slow window of sales, split it; if it
    finished ok, read the file, apply it and queue the next step."""
    if job.status == "error":
        _after_failure(session, job, today or datetime.now(UTC).date())
        return
    if job.status != "ok" or not job.r2_key:
        return
    counts = dict(job.counts or {})
    if "applied" in counts:
        return  # a repeated callback must not apply twice
    company = session.get(TallyCompany, job.company_id)
    step = str(counts.get("step", ""))
    today = today or datetime.now(UTC).date()
    if company is None or not step:
        return
    try:
        raw = get_object(job.r2_key)
        if step == "groups":
            groups = {g.name: g.parent for g in parse_groups(raw)}
            counts["groups_map"] = groups
            result = _Counted(groups=len(groups))
        elif step == "items":
            item_rows = parse_stock_items(raw)
            result = apply_stock(
                session, company, item_rows, complete=counts.get("mode", "full") == "full"
            )
            company.items_alter_id = _advance(company.items_alter_id, [r.alter_id for r in item_rows])
        elif step == "ledgers":
            known = [TallyGroup(name=n, parent=p) for n, p in (counts.get("groups_map") or {}).items()]
            ledger_rows = parse_ledgers(raw)
            result = apply_outstanding(
                session,
                company,
                balances_from_ledgers(ledger_rows, known),
                today,
                absent_is_zero=False,
                complete=counts.get("mode", "full") == "full",
            )
            company.ledgers_alter_id = _advance(company.ledgers_alter_id, [r.alter_id for r in ledger_rows])
        elif step == "bills":
            result = apply_bill_dates(session, company, parse_bills_receivable(raw))
        elif step == "vtypes":
            types = parse_voucher_types(raw)
            counts["voucher_types"] = types
            result = _Types(voucher_types=len(types))
        else:
            result = apply_sales(
                session, company, parse_vouchers(raw, counts.get("voucher_types") or None)
            )
    except (R2NotConfigured, ValueError, OSError) as exc:
        complete_job_error(session, job, error=f"could not read the Tally file: {exc}"[:500])
        return
    except Exception as exc:  # a bad file must end this job, never the agent's callback
        log.exception("refill %s: apply failed", job.id)
        complete_job_error(session, job, error=f"could not apply the Tally data: {exc}"[:500])
        return

    done = dict(counts.get("refill") or {})
    done[step] = _summary(result)
    counts["refill"] = done
    counts["applied"] = True
    job.counts = counts
    session.flush()

    left = list(counts.get("steps_left") or [])
    if step.startswith("sales:"):
        start, end = window_of(step, today)
        size = (end - start).days + 1
        counts["window_days"] = size  # this size worked
        ms = counts.get("ms")
        if isinstance(ms, int) and ms < SALES_FAST_MS and size < SALES_MAX_WINDOW and left:
            grown = min(SALES_MAX_WINDOW, size * 2)
            left = _resize(left, grown, today)
            counts["window_days"] = grown
        job.counts = counts
    if counts.get("mode", "full") == "full" and (step == "bills" or not left):
        # the masters side of a complete read is in (sales follow and may be slow): deletions are caught up
        company.full_refill_at = datetime.now(UTC)
    if left:
        counts["steps_left"] = left
        _queue_next(session, job, company, left, done, counts, today)


def _carry(counts: dict) -> dict:
    return {
        "groups_map": counts.get("groups_map") or {},
        "voucher_types": counts.get("voucher_types") or {},
        "notes": counts.get("notes") or [],
        "timeouts": counts.get("timeouts") or 0,
        "window_days": counts.get("window_days"),
    }


def _queue_next(session, job, company, steps, done, counts, today) -> None:  # type: ignore[no-untyped-def]
    try:
        _enqueue(session, company, steps, done, today, _carry(counts), counts.get("mode", "full"))
    except Exception:  # queuing the next step must not undo what this one wrote
        log.exception("refill %s: could not queue the next step", job.id)


def _after_failure(session: Session, job: TallySyncJob, today: date) -> None:
    """A step failed. A sales window Tally was too slow for is split and read again; anything else ends the
    chain with the error already on the job (shown on the company card)."""
    counts = dict(job.counts or {})
    step = str(counts.get("step", ""))
    if counts.get("handled") or not step.startswith("sales:") or TIMEOUT_TEXT not in (job.error or ""):
        return
    company = session.get(TallyCompany, job.company_id)
    if company is None:
        return
    start, end = window_of(step, today)
    label = f"{start:%d %b} to {end:%d %b %Y}" if start != end else f"{start:%d %b %Y}"
    notes = list(counts.get("notes") or [])
    timeouts = int(counts.get("timeouts") or 0) + 1
    left = list(counts.get("steps_left") or [])
    days = (end - start).days + 1
    steps: list[str] = []
    if timeouts >= MAX_TIMEOUTS:
        notes.append(
            f"Tally was too slow to answer for sales {label}, and it has been slow {timeouts} times in this read, "
            "so the remaining sales were left. Stock, balances and bill dates are in. Sales rates need Tally to "
            "answer faster (a smaller Tally data file, or a longer wait in the agent)."
        )
    elif days > 1:
        size = max(1, days // 2)
        notes.append(f"Tally was slow on sales for {label}: reading {size} day{'s' if size > 1 else ''} at a time.")
        steps = _resize([step] + left, size, today)
        counts["window_days"] = size
    else:
        notes.append(f"Tally could not answer for sales on {label} even for one day: skipped.")
        steps = left
    log.warning("refill %s: sales %s timed out (%d in this read): %s", job.id, label, timeouts, notes[-1])
    counts.update(handled=True, notes=notes, timeouts=timeouts)
    job.counts = counts
    session.flush()
    if steps:
        counts["steps_left"] = steps[1:]
        job.counts = counts
        _queue_next(session, job, company, steps, dict(counts.get("refill") or {}), counts, today)

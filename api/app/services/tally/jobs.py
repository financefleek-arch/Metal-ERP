"""Tally Connector sync-job lifecycle + the "one pull in flight" guard.

A `tally_sync_job` of kind `pull_masters` is created alongside an
`AgentOutboxItem` the companion agent drains on its next checkin. The agent
reads the shop's Tally export folder, uploads the XML to R2, and calls
`POST /api/tally-agent/jobs/{id}/result`, which stages a batch and marks the
job `ok` / `error`.

`assert_no_pull_in_flight` is shared by `POST /api/tally/pull-masters` and
the two manual file-upload routes so the three can't stomp each other's
staging batch (the staging tables allow only one un-committed batch per
tenant — a policy, not a schema rule).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentOutboxItem,
    StagingTallyItem,
    StagingTallyParty,
    TallyCompany,
    TallySyncJob,
)

# job.status values that mean "the agent still owes us a result"
_NON_TERMINAL = ("queued", "sent", "running")

# A pull that has been `sent` this long with only "not ready" agent pings
# (Tally closed / no company open) and no real result is auto-cancelled on
# the next pull attempt, so a mis-timed pull can't wedge the tenant forever.
_STUCK_AFTER = timedelta(minutes=10)


def _expire_stuck_pull(session: Session, job: TallySyncJob) -> bool:
    """Lazy auto-cancel: if `job` is a `sent` pull older than the window whose
    agent only ever reported a 'not ready' status, move it to `error` and
    return True (the caller may then proceed with a fresh pull).
    """
    if job.status != "sent" or job.kind != "pull_masters":
        return False
    age = datetime.now(UTC) - _as_utc(job.created_at)
    if age < _STUCK_AFTER:
        return False
    # only expire if the agent actually saw it and kept saying "not ready"
    if job.last_agent_status not in ("no_company_loaded", "tally_unavailable"):
        return False
    job.status = "error"
    job.error = "Tally stayed unavailable — retried, then cancelled."
    job.completed_at = datetime.now(UTC)
    session.flush()
    return True


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def assert_no_pull_in_flight(session: Session, tenant_id: str) -> None:
    """409 if this tenant has a masters pull that is either still running or
    finished-ok with staged rows the operator hasn't committed or discarded
    yet. Keeps a second pull — or a manual file upload — from wiping the
    un-reviewed batch.
    """
    running = session.scalar(
        select(TallySyncJob).where(
            TallySyncJob.tenant_id == tenant_id,
            TallySyncJob.kind == "pull_masters",
            TallySyncJob.status.in_(_NON_TERMINAL),
        )
    )
    if running is not None and not _expire_stuck_pull(session, running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A Tally masters pull is already in progress. Wait for it to "
                "finish before starting another."
            ),
        )

    uncommitted = session.scalar(
        select(StagingTallyParty.id).where(
            StagingTallyParty.tenant_id == tenant_id,
            StagingTallyParty.committed_as.is_(None),
            StagingTallyParty.sync_job_id.is_not(None),
        )
    ) or session.scalar(
        select(StagingTallyItem.id).where(
            StagingTallyItem.tenant_id == tenant_id,
            StagingTallyItem.committed_as.is_(None),
            StagingTallyItem.sync_job_id.is_not(None),
        )
    )
    if uncommitted is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A Tally masters pull is waiting for review. Commit or discard "
                "that batch before starting another."
            ),
        )


def enqueue_pull_masters(
    session: Session, company: TallyCompany
) -> TallySyncJob:
    """Create the job + the paired agent outbox item. The caller has already
    run `assert_no_pull_in_flight`.
    """
    if company.shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Link a companion-agent install to this Tally company first.",
        )

    job = TallySyncJob(
        tenant_id=company.tenant_id,
        company_id=company.id,
        direction="in",
        kind="pull_masters",
        status="queued",
    )
    session.add(job)
    session.flush()

    outbox = AgentOutboxItem(
        shop_id=company.shop_id,
        module="tally",
        payload={
            "job_id": job.id,
            "action": "pull_masters",
            "company_name": company.company_name,
        },
        status="queued",
    )
    session.add(outbox)
    session.flush()

    job.outbox_item_id = outbox.id
    session.flush()
    return job


def mark_job_sent(session: Session, job: TallySyncJob) -> None:
    """The agent picked the outbox item up (seen when checkin flips the
    outbox row to `sent`). Best-effort — not every transport reports this.
    """
    if job.status == "queued":
        job.status = "sent"
        session.flush()


def record_agent_status(
    session: Session, job: TallySyncJob, agent_status: str
) -> None:
    """The agent pinged a 'not ready' reason (Tally closed / no company
    open) instead of a real result — record it for the Pull step indicator
    and the lazy auto-cancel. Only meaningful while the job is non-terminal.
    """
    if job.status in ("ok", "error"):
        return
    job.last_agent_status = agent_status[:32]
    job.last_agent_status_at = datetime.now(UTC)
    if job.status == "queued":
        job.status = "sent"
    session.flush()


def complete_job_ok(
    session: Session, job: TallySyncJob, *, batch_id: str, counts: dict
) -> None:
    job.status = "ok"
    job.batch_id = batch_id
    job.counts = counts
    job.completed_at = datetime.now(UTC)
    session.flush()


def complete_job_error(session: Session, job: TallySyncJob, *, error: str) -> None:
    job.status = "error"
    job.error = error[:2000]
    job.completed_at = datetime.now(UTC)
    session.flush()

"""Process a completed `pull_masters` job: download the agent's XML from R2,
parse + stage parties and stock items, derive `known_ledgers`, mark the job.

Called from the agent-facing result callback
(`POST /api/tally-agent/jobs/{id}/result`). All failures are turned into a
job `error` — this path must never 500 back at the agent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.backup_storage import R2NotConfigured, get_object
from app.models import TallyCompany, TallySyncJob
from app.services.tally.jobs import complete_job_error, complete_job_ok
from app.services.tally.staging import (
    NoLedgersError,
    NoStockItemsError,
    stage_masters_xml,
    stage_stock_items_xml,
)

log = logging.getLogger("tally.pull")


def process_pull_result(
    session: Session,
    job: TallySyncJob,
    *,
    ok: bool,
    r2_key: str | None,
    agent_error: str | None,
) -> None:
    """Advance `job` to `ok` or `error`. Idempotent: a second call on a
    terminal job is a no-op.
    """
    if job.status in ("ok", "error"):
        return

    if not ok:
        complete_job_error(
            session, job, error=agent_error or "agent reported a failure"
        )
        return

    if not r2_key:
        complete_job_error(session, job, error="agent result missing r2_key")
        return

    job.status = "running"
    job.r2_key = r2_key
    session.flush()

    try:
        raw = get_object(r2_key)
    except R2NotConfigured as e:
        complete_job_error(session, job, error=f"cloud storage not configured: {e}")
        return
    except Exception as e:  # noqa: BLE001 - any download failure -> job error
        log.exception("tally pull %s: R2 download failed", job.id)
        complete_job_error(session, job, error=f"could not download masters XML: {e}")
        return

    # One shared batch id across the party + item staging so the review UI
    # and the "discard" both cover the whole pull.
    batch_id = str(uuid.uuid4())
    counts: dict[str, int] = {}

    try:
        party_summary = stage_masters_xml(
            session, job.tenant_id, raw, batch_id=batch_id, sync_job_id=job.id
        )
        counts["ledgers"] = party_summary.total
    except NoLedgersError:
        # A stock-only export is legitimate; keep going, note zero ledgers.
        counts["ledgers"] = 0
        party_summary = None
    except ValueError as e:
        complete_job_error(session, job, error=str(e))
        return

    try:
        item_summary = stage_stock_items_xml(
            session, job.tenant_id, raw, batch_id=batch_id, sync_job_id=job.id
        )
        counts["items"] = item_summary.total
        counts["dummies_skipped"] = item_summary.dummies_skipped
    except NoStockItemsError:
        counts["items"] = 0
    except ValueError as e:
        # Parties may have staged fine; still surface the item parse failure.
        complete_job_error(
            session, job, error=f"stock-item parse failed: {e}"
        )
        return

    if counts.get("ledgers", 0) == 0 and counts.get("items", 0) == 0:
        complete_job_error(
            session,
            job,
            error="the export contained no ledgers or stock items",
        )
        return

    # Derive known_ledgers for the ledger-map dropdowns.
    company = session.get(TallyCompany, job.company_id)
    if company is not None:
        if party_summary is not None and party_summary.known_ledgers:
            company.known_ledgers = party_summary.known_ledgers
        company.last_masters_pull_at = datetime.now(UTC)
        session.flush()

    complete_job_ok(session, job, batch_id=batch_id, counts=counts)

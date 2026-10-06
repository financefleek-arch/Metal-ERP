"""One place to see what is running in the background: builds, label prints, Tally sends."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep
from app.models import CatalogOutputJob
from app.services.catalog import tally_items

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_LABELS = {
    "labels": "Printing labels",
    "item_labels": "Printing labels",
    "customer_catalog": "Building a customer catalog",
}


class ActiveJob(BaseModel):
    kind: str  # builds | tally
    title: str
    detail: str | None = None
    progress: int | None = None
    total: int | None = None
    # where to look at it
    link: str | None = None


@router.get("/active", response_model=list[ActiveJob])
def active_jobs(user: CurrentUser, session: SessionDep) -> list[ActiveJob]:
    """What is queued or running for this firm. Small and cheap: it is polled."""
    out: list[ActiveJob] = []
    for job in session.scalars(
        select(CatalogOutputJob)
        .where(
            CatalogOutputJob.tenant_id == user.tenant_id,
            CatalogOutputJob.status.in_(("queued", "running")),
        )
        .order_by(CatalogOutputJob.created_at)
        .limit(10)
    ):
        out.append(
            ActiveJob(
                kind="builds",
                title=_LABELS.get(job.kind, "Building a file"),
                detail="queued" if job.status == "queued" else None,
                progress=job.progress,
                total=job.total,
                link="/items/catalogs" if job.kind == "customer_catalog" else None,
            )
        )
    run = tally_items.latest_run(session, user.tenant_id)
    if run.state == "running":
        detail = {
            "waiting_agent": "waiting for the Tally agent",
            "waiting_tally": "waiting for TallyPrime to be ready",
        }.get(run.phase or "")
        out.append(
            ActiveJob(
                kind="tally",
                title="Sending items to Tally",
                detail=detail,
                progress=run.synced,
                total=run.total_items,
                link="/items",
            )
        )
    return out

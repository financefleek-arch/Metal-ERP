"""Share links, from the shop's side (same gate as the catalog module), and the public page's data.

The public endpoint needs no login: the token in the URL is the key.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import SessionDep
from app.models import CatalogShareLink
from app.routers.catalog import CatalogUser, CatalogWriteUser
from app.schemas_customer_catalog import CatalogSelection
from app.services import audit
from app.services import share_links as svc
from app.services.item_filter import resolve_ids
from app.services.ratelimit import RateLimiter, limit_dependency

router = APIRouter(prefix="/api/share-links", tags=["share-links"])
public_router = APIRouter(prefix="/api/public", tags=["public"])

# one catalog view is a lot of data: a person browsing needs far fewer than this
_public_limiter = RateLimiter(limit=60, window_seconds=60)
public_limit = Depends(limit_dependency(_public_limiter))


class ShareLinkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    selection: CatalogSelection
    include_expected: bool = False
    expires_at: datetime | None = None


class ShareLinkPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    include_expected: bool | None = None
    # send null to remove the expiry
    expires_at: datetime | None = None
    revoked: bool | None = None


class ShareLinkOut(BaseModel):
    id: str
    title: str
    token: str
    path: str  # the part of the URL after the site address
    include_expected: bool
    expires_at: datetime | None
    revoked_at: datetime | None
    live: bool
    view_count: int
    last_viewed_at: datetime | None
    created_at: datetime
    selection_kind: str


def _out(link: CatalogShareLink) -> ShareLinkOut:
    return ShareLinkOut(
        id=link.id,
        title=link.title,
        token=link.token,
        path=f"/c/{link.token}",
        include_expected=link.include_expected,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        live=svc.is_live(link),
        view_count=link.view_count,
        last_viewed_at=link.last_viewed_at,
        created_at=link.created_at,
        selection_kind="filter" if "filter" in link.selection_json else "ids",
    )


def _get(session: SessionDep, tenant_id: str, link_id: str) -> CatalogShareLink:
    link = session.scalar(
        select(CatalogShareLink).where(
            CatalogShareLink.id == link_id, CatalogShareLink.tenant_id == tenant_id
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    return link


@router.post("", response_model=ShareLinkOut, status_code=status.HTTP_201_CREATED)
def create_link(body: ShareLinkCreate, session: SessionDep, user: CatalogWriteUser) -> ShareLinkOut:
    ids = resolve_ids(session, user.tenant_id, body.selection.ids, body.selection.filter)
    if not ids:
        raise HTTPException(status_code=422, detail="Select the items to share first.")
    if body.expires_at is not None and svc._utc(body.expires_at) <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="The expiry must be in the future.")
    link = svc.create(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=body.title,
        selection=body.selection,
        include_expected=body.include_expected,
        expires_at=body.expires_at,
    )
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="share_link",
        entity_id=link.id,
        action="create",
        after={"title": link.title, "items": len(ids)},
    )
    return _out(link)


@router.get("", response_model=list[ShareLinkOut])
def list_links(session: SessionDep, user: CatalogUser) -> list[ShareLinkOut]:
    rows = session.scalars(
        select(CatalogShareLink)
        .where(CatalogShareLink.tenant_id == user.tenant_id)
        .order_by(CatalogShareLink.created_at.desc())
        .limit(200)
    )
    return [_out(r) for r in rows]


@router.patch("/{link_id}", response_model=ShareLinkOut)
def patch_link(
    link_id: str, body: ShareLinkPatch, session: SessionDep, user: CatalogWriteUser
) -> ShareLinkOut:
    link = _get(session, user.tenant_id, link_id)
    patch = body.model_fields_set
    if "title" in patch and body.title:
        link.title = body.title.strip()
    if "include_expected" in patch and body.include_expected is not None:
        link.include_expected = body.include_expected
    if "expires_at" in patch:
        if body.expires_at is not None and svc._utc(body.expires_at) <= datetime.now(UTC):
            raise HTTPException(status_code=422, detail="The expiry must be in the future.")
        link.expires_at = body.expires_at
    if "revoked" in patch and body.revoked is not None:
        # stopping is immediate; starting again is allowed so a mistaken stop can be undone
        link.revoked_at = datetime.now(UTC) if body.revoked else None
        audit.record(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            entity="share_link",
            entity_id=link.id,
            action="stop" if body.revoked else "resume",
        )
    session.flush()
    return _out(link)


@public_router.get("/catalog/{token}", dependencies=[public_limit])
def public_catalog(token: str, session: SessionDep) -> dict[str, object]:
    """What a customer sees. Unknown, stopped and expired links all answer the same 404."""
    link = svc.find_live(session, token)
    if link is None:
        raise HTTPException(status_code=404, detail="This link is not available.")
    view = svc.public_view(session, link)
    svc.record_view(session, link)
    return view

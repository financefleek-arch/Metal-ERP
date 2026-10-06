"""Public catalog links: create, look up, and build what a customer is allowed to see.

The public view is an **allow-list**: a handful of fields per item and nothing else. Cost price,
supplier, margin, stock figures, Tally state and other customers never reach it, and a test pins
the exact set of keys.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import CatalogShareLink, Item, ItemCategory, Tenant
from app.models._mixins import Availability
from app.schemas_customer_catalog import CatalogLayout, CatalogSelection
from app.services.catalog import item_catalogs
from app.services.media_urls import media_url

# A customer page loads the whole catalog at once; this keeps one response a sensible size.
MAX_PUBLIC_ITEMS = 3000
ON_ORDER = "On order"

PUBLIC_ITEM_FIELDS = (
    "id",
    "name",
    "code",
    "group",
    "price",
    "pack_qty",
    "photo_url",
    "thumb_url",
    "tag",
)


def new_token() -> str:
    """96 bits of randomness, URL-safe (16 characters)."""
    return secrets.token_urlsafe(12)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def is_live(link: CatalogShareLink, now: datetime | None = None) -> bool:
    if link.revoked_at is not None:
        return False
    return link.expires_at is None or _utc(link.expires_at) > (now or datetime.now(UTC))


def find_live(session: Session, token: str) -> CatalogShareLink | None:
    """The link for this token if it can still be used; None for unknown, revoked and expired
    alike (the caller answers all three the same way)."""
    if not token or len(token) > 40:
        return None
    link = session.scalar(select(CatalogShareLink).where(CatalogShareLink.token == token))
    return link if link is not None and is_live(link) else None


def create(
    session: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    title: str,
    selection: CatalogSelection,
    include_expected: bool,
    expires_at: datetime | None,
) -> CatalogShareLink:
    link = CatalogShareLink(
        tenant_id=tenant_id,
        created_by=user_id,
        token=new_token(),
        title=title.strip(),
        selection_json=selection.model_dump(mode="json", exclude_none=True),
        include_expected=include_expected,
        expires_at=expires_at,
    )
    session.add(link)
    session.flush()
    return link


def selection_of(link: CatalogShareLink) -> CatalogSelection:
    return CatalogSelection.model_validate(link.selection_json)


def record_view(session: Session, link: CatalogShareLink) -> None:
    session.execute(
        update(CatalogShareLink)
        .where(CatalogShareLink.id == link.id)
        .values(view_count=CatalogShareLink.view_count + 1, last_viewed_at=datetime.now(UTC))
    )


def _price_text(item: Item) -> str:
    return f"{Decimal(str(item.default_rate)).quantize(Decimal('0.01')):.2f}"


def public_view(session: Session, link: CatalogShareLink) -> dict[str, object]:
    """Everything the customer page needs, and nothing it must not have."""
    tenant = session.get(Tenant, link.tenant_id)
    assert tenant is not None
    layout = CatalogLayout(include_expected=link.include_expected)
    ids = item_catalogs.resolve_selection(session, link.tenant_id, selection_of(link))
    plan = item_catalogs.plan(session, link.tenant_id, ids, layout)
    cats = dict(
        session.execute(
            select(ItemCategory.id, ItemCategory.name).where(
                ItemCategory.tenant_id == link.tenant_id
            )
        ).all()
    )
    items: list[dict[str, object]] = []
    groups: list[str] = []
    for it in plan.items[:MAX_PUBLIC_ITEMS]:
        group = cats.get(it.category_id or "") or "Other"
        if group not in groups:
            groups.append(group)
        items.append(
            {
                "id": it.id,
                "name": it.name,
                "code": it.sku or it.barcode or "",
                "group": group,
                "price": _price_text(it),
                "pack_qty": int(it.pack_qty or 1),
                "photo_url": media_url(it.primary_media_id, "photo")
                if it.primary_media_id
                else None,
                "thumb_url": media_url(it.primary_media_id, "thumb")
                if it.primary_media_id
                else None,
                "tag": ON_ORDER if it.availability == Availability.expected.value else None,
            }
        )
    return {
        "title": link.title,
        "firm_name": tenant.trade_name or tenant.legal_name,
        "firm_phone": tenant.phone,
        "terms_line": tenant.order_terms_line,
        "min_order": (
            f"{Decimal(str(tenant.order_min_value)).quantize(Decimal('0.01')):.2f}"
            if tenant.order_min_value
            else None
        ),
        "groups": groups,
        "items": items,
    }

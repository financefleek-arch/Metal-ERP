"""Deepgram keyterm-prompting source: this tenant's own most-billed item
names, biasing the acoustic model toward the shop's real vocabulary (fixes
mishears like "wine glass" -> "vine gloves" on terms it has no domain
context for). Capped well under Nova-3's 500-token keyterm budget."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item
from app.models._mixins import ItemStatus

_TOP_N = 100


def top_item_keyterms(session: Session, tenant_id: str) -> list[str]:
    rows = session.scalars(
        select(Item.name)
        .where(Item.tenant_id == tenant_id, Item.status != ItemStatus.archived)
        .order_by(Item.times_billed.desc())
        .limit(_TOP_N)
    ).all()
    return list(rows)

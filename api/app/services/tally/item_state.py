"""What one Tally company holds for an item (S1.2b): push state and prices, one row per (company, item).

`Item.tally_status` / `tally_price` / `tally_seen_price` / `tally_pushed_at` are read-only views of the
firm's DEFAULT company (models/__init__.py), for lists and filters. Every push and check works on the
company it was started for and reads and writes through here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ItemTallyState, TallyCompany

_CHUNK = 500


def state_map(
    session: Session, company: TallyCompany, item_ids: Sequence[str]
) -> dict[str, ItemTallyState]:
    out: dict[str, ItemTallyState] = {}
    ids = list(item_ids)
    for i in range(0, len(ids), _CHUNK):
        for row in session.scalars(
            select(ItemTallyState).where(
                ItemTallyState.company_id == company.id,
                ItemTallyState.item_id.in_(ids[i : i + _CHUNK]),
            )
        ):
            out[row.item_id] = row
    return out


def synced_item_ids(company: TallyCompany):  # type: ignore[no-untyped-def]
    """A subquery of the items this company has been sent (state `synced`), for `Item.id.in_(...)`."""
    return select(ItemTallyState.item_id).where(
        ItemTallyState.company_id == company.id, ItemTallyState.status == "synced"
    )


def _row(
    session: Session, company: TallyCompany, item_id: str, existing: dict[str, ItemTallyState]
) -> ItemTallyState:
    row = existing.get(item_id)
    if row is None:
        row = ItemTallyState(
            tenant_id=company.tenant_id, company_id=company.id, item_id=item_id, status="none"
        )
        session.add(row)
        existing[item_id] = row
    return row


def set_status(
    session: Session, company: TallyCompany, item_ids: Sequence[str], value: str
) -> None:
    """Move items between none / queued / error / synced for `company`. An item Tally already has stays
    synced through a price update that is queued, fails or is cancelled: only first sends move between
    the states."""
    ids = list(item_ids)
    if not ids:
        return
    rows = state_map(session, company, ids)
    for item_id in ids:
        current = rows[item_id].status if item_id in rows else "none"
        if value == "queued" and current == "synced":
            continue
        if value in ("none", "error") and current != "queued":
            continue
        _row(session, company, item_id, rows).status = value
    session.flush()


def record_sent(
    session: Session,
    company: TallyCompany,
    item_ids: Sequence[str],
    prices: dict[str, str],
    now: datetime,
) -> None:
    """The batch was accepted: synced, with the price sent; what Tally shows is unknown until checked."""
    ids = list(item_ids)
    rows = state_map(session, company, ids)
    for item_id in ids:
        row = _row(session, company, item_id, rows)
        row.status = "synced"
        p = prices.get(item_id)
        row.price = Decimal(p) if p is not None else None
        row.pushed_at = now
        row.seen_price = None
    session.flush()


def record_seen(
    session: Session, company: TallyCompany, seen_by_item: dict[str, Decimal]
) -> None:
    rows = state_map(session, company, list(seen_by_item))
    for item_id, seen in seen_by_item.items():
        _row(session, company, item_id, rows).seen_price = seen
    session.flush()

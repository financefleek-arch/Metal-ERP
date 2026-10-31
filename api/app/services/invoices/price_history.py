"""What a party was last billed for an item (Tally's Stock Query, Alt+S, in the invoice editor).

Reads finalized sales invoices only: drafts and cancelled invoices are not real sales, credit
notes are not a selling price. Keyed by `item_id`, so a line typed free (no item yet) has no
history until it is finalized and linked, which is fine: its first sale has none to show.

Sales made in Tally before the shop came here arrive differently (plan 2026-10-31, the refill): not as
invoices but as one row per party and item in `party_item_price`, the latest rate Tally knows of. They are
merged in here, labelled `source="tally"`; a later native sale always wins over an older Tally one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceLine, PartyItemPrice
from app.models._mixins import DocType, InvoiceStatus

MAX_HISTORY = 20


@dataclass(frozen=True)
class PricePoint:
    invoice_id: str
    series: str
    number: int | None
    date: date
    item_id: str
    quantity: Decimal
    uom: str | None
    unit_rate: Decimal
    discount_pct: Decimal | None
    source: str = "native"


def _base(tenant_id: str, party_id: str):
    return (
        select(
            Invoice.id,
            Invoice.series,
            Invoice.number,
            Invoice.date,
            InvoiceLine.item_id,
            InvoiceLine.quantity,
            InvoiceLine.uom,
            InvoiceLine.unit_rate,
            InvoiceLine.discount_pct,
        )
        .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.party_id == party_id,
            Invoice.status == InvoiceStatus.final,
            Invoice.doc_type == DocType.inv,
            InvoiceLine.item_id.is_not(None),
        )
    )


def _point(r) -> PricePoint:
    return PricePoint(
        invoice_id=r[0], series=r[1], number=r[2], date=r[3], item_id=r[4],
        quantity=r[5], uom=r[6], unit_rate=r[7], discount_pct=r[8],
    )


def _tally_point(row: PartyItemPrice) -> PricePoint:
    return PricePoint(
        invoice_id=f"tally:{row.id}",
        series=f"Tally {row.voucher_no}" if row.voucher_no else "Tally",
        number=None,
        date=row.sold_on,
        item_id=row.item_id,
        quantity=row.quantity or Decimal(0),
        uom=row.unit,
        unit_rate=row.rate,
        discount_pct=None,
        source="tally",
    )


def _tally_rows(
    session: Session, tenant_id: str, party_id: str, item_ids: list[str], company_ids: list[str] | None
) -> list[PartyItemPrice]:
    """What Tally last sold these items to this party. `company_ids` limits it to the companies the
    caller may see (S1.7); None means no limit."""
    if company_ids is not None and not company_ids:
        return []
    q = select(PartyItemPrice).where(
        PartyItemPrice.tenant_id == tenant_id,
        PartyItemPrice.party_id == party_id,
        PartyItemPrice.item_id.in_(item_ids),
    )
    if company_ids is not None:
        q = q.where(PartyItemPrice.company_id.in_(company_ids))
    return list(session.scalars(q))


def item_history(
    session: Session,
    tenant_id: str,
    party_id: str,
    item_id: str,
    limit: int = 10,
    company_ids: list[str] | None = None,
) -> list[PricePoint]:
    """Newest first. One row per invoice line, so an item billed twice on one bill shows twice. Tally's
    last sale, when it has one, is part of the list."""
    limit = max(1, min(limit, MAX_HISTORY))
    q = (
        _base(tenant_id, party_id)
        .where(InvoiceLine.item_id == item_id)
        .order_by(Invoice.date.desc(), Invoice.number.desc().nulls_last(), InvoiceLine.sl_no)
        .limit(limit)
    )
    points = [_point(r) for r in session.execute(q).all()]
    points += [_tally_point(r) for r in _tally_rows(session, tenant_id, party_id, [item_id], company_ids)]
    points.sort(key=lambda p: p.date, reverse=True)  # stable: a native sale stays first on a tie
    return points[:limit]


def last_prices(
    session: Session,
    tenant_id: str,
    party_id: str,
    item_ids: list[str],
    company_ids: list[str] | None = None,
) -> dict[str, PricePoint]:
    """The most recent sale of each item to this party (items never sold to them are absent). A native
    sale wins over a Tally one of the same date or older."""
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        return {}
    ranked = (
        _base(tenant_id, party_id)
        .where(InvoiceLine.item_id.in_(ids))
        .add_columns(
            func.row_number()
            .over(
                partition_by=InvoiceLine.item_id,
                order_by=(
                    Invoice.date.desc(),
                    Invoice.number.desc().nulls_last(),
                    InvoiceLine.sl_no.desc(),
                ),
            )
            .label("rn")
        )
        .subquery()
    )
    rows = session.execute(select(ranked).where(ranked.c.rn == 1)).all()
    found = {r[4]: _point(r) for r in rows}
    for row in _tally_rows(session, tenant_id, party_id, ids, company_ids):
        have = found.get(row.item_id)
        if have is None or row.sold_on > have.date:
            found[row.item_id] = _tally_point(row)
    return found

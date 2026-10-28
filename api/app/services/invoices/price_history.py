"""What a party was last billed for an item (Tally's Stock Query, Alt+S, in the invoice editor).

Reads finalized sales invoices only: drafts and cancelled invoices are not real sales, credit
notes are not a selling price. Keyed by `item_id`, so a line typed free (no item yet) has no
history until it is finalized and linked, which is fine: its first sale has none to show.

Imported Tally sales (plan `PLAN-tally-voucher-import-and-party-prices.md`) will be native
`invoice` rows, so they flow in here with no change; `source` already tells the two apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceLine
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


def item_history(
    session: Session, tenant_id: str, party_id: str, item_id: str, limit: int = 10
) -> list[PricePoint]:
    """Newest first. One row per invoice line, so an item billed twice on one bill shows twice."""
    q = (
        _base(tenant_id, party_id)
        .where(InvoiceLine.item_id == item_id)
        .order_by(Invoice.date.desc(), Invoice.number.desc().nulls_last(), InvoiceLine.sl_no)
        .limit(max(1, min(limit, MAX_HISTORY)))
    )
    return [_point(r) for r in session.execute(q).all()]


def last_prices(
    session: Session, tenant_id: str, party_id: str, item_ids: list[str]
) -> dict[str, PricePoint]:
    """The most recent sale of each item to this party (items never sold to them are absent)."""
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
    return {r[4]: _point(r) for r in rows}

"""Supplier price history: what a supplier quotes (price list) and what you paid (bill).

One row per observation, written when a price list is imported and when a bill is approved. It
feeds the "price changed since the last list" marks on a re-import, "last bought at" on a price
list row, and the "bill price is above the quote" flag when a bill is read.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SupplierPricePoint

_CHUNK = 500
# a bill rate this much over the latest quote (percent) is worth a second look
ABOVE_QUOTE_PCT = Decimal("2")


def _money(v: object) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def latest_quotes(
    session: Session,
    tenant_id: str,
    product_ids: Sequence[str],
    *,
    exclude_source_id: str | None = None,
) -> dict[str, Decimal]:
    """product id -> the most recent quoted price (per pack), ignoring one catalog."""
    out: dict[str, Decimal] = {}
    ids = list(product_ids)
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        stmt = (
            select(SupplierPricePoint)
            .where(
                SupplierPricePoint.tenant_id == tenant_id,
                SupplierPricePoint.source == "quote",
                SupplierPricePoint.product_id.in_(chunk),
            )
            .order_by(SupplierPricePoint.on_date, SupplierPricePoint.created_at)
        )
        if exclude_source_id:
            stmt = stmt.where(SupplierPricePoint.source_id != exclude_source_id)
        for p in session.scalars(stmt):  # oldest first, so the newest wins
            if p.product_id:
                out[p.product_id] = _money(p.price)
    return out


def change_of(previous: Decimal | None, now: Decimal) -> str:
    if previous is None:
        return "new"
    if now > previous:
        return "up"
    if now < previous:
        return "down"
    return "same"


def record_quotes(
    session: Session,
    *,
    tenant_id: str,
    supplier_party_id: str | None,
    catalog_id: str,
    rows: Iterable[tuple[str, Decimal, int | None]],
    on: date,
) -> None:
    """`rows` are (product id, quoted price, pack qty)."""
    for product_id, price, pack in rows:
        session.add(
            SupplierPricePoint(
                tenant_id=tenant_id,
                supplier_party_id=supplier_party_id,
                product_id=product_id,
                source="quote",
                source_id=catalog_id,
                price=price,
                pack_qty=pack,
                on_date=on,
            )
        )


def record_bill_prices(
    session: Session,
    *,
    tenant_id: str,
    supplier_party_id: str | None,
    bill_id: str,
    rows: Iterable[tuple[str, Decimal]],
    on: date,
) -> None:
    """`rows` are (item id, rate paid per unit)."""
    for item_id, price in rows:
        session.add(
            SupplierPricePoint(
                tenant_id=tenant_id,
                supplier_party_id=supplier_party_id,
                item_id=item_id,
                source="bill",
                source_id=bill_id,
                price=price,
                on_date=on,
            )
        )


def last_paid(
    session: Session, tenant_id: str, item_ids: Sequence[str]
) -> dict[str, tuple[Decimal, date]]:
    """item id -> (rate paid, date) of the latest bill."""
    out: dict[str, tuple[Decimal, date]] = {}
    ids = list(dict.fromkeys(item_ids))
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        for p in session.scalars(
            select(SupplierPricePoint)
            .where(
                SupplierPricePoint.tenant_id == tenant_id,
                SupplierPricePoint.source == "bill",
                SupplierPricePoint.item_id.in_(chunk),
            )
            .order_by(SupplierPricePoint.on_date, SupplierPricePoint.created_at)
        ):
            if p.item_id:
                out[p.item_id] = (_money(p.price), p.on_date)
    return out


def latest_quote_for_item(
    session: Session, tenant_id: str, item_id: str, supplier_party_id: str | None
) -> list[Decimal]:
    """What the supplier last quoted for this item, as the prices a bill might legitimately show:
    the price of a pack, and the price of one piece. Empty when there is no quote."""
    from app.models import CatalogProduct

    stmt = (
        select(SupplierPricePoint)
        .join(CatalogProduct, CatalogProduct.id == SupplierPricePoint.product_id)
        .where(
            SupplierPricePoint.tenant_id == tenant_id,
            SupplierPricePoint.source == "quote",
            CatalogProduct.item_id == item_id,
        )
        .order_by(SupplierPricePoint.on_date.desc(), SupplierPricePoint.created_at.desc())
        .limit(1)
    )
    if supplier_party_id:
        stmt = stmt.where(SupplierPricePoint.supplier_party_id == supplier_party_id)
    p = session.scalar(stmt)
    if p is None:
        return []
    out = [_money(p.price)]
    pack = int(p.pack_qty or 1)
    if pack > 1:
        out.append(_money(Decimal(str(p.price)) / pack))
    return out


def bill_flag(rate: Decimal | None, quotes: Sequence[Decimal]) -> str | None:
    """`above` when the bill's rate is more than the tolerance over every reading of the quote."""
    if rate is None or not quotes:
        return None
    limit = Decimal("1") + ABOVE_QUOTE_PCT / Decimal("100")
    if any(Decimal(str(rate)) <= q * limit for q in quotes):
        return None
    return "above"

"""Customer orders: placing one from a share link, and reading it back.

An order is a request made on the public page. The server decides everything that matters: prices
come from the items (never from the browser), only items the link currently offers can be
ordered, the minimum order is enforced, and a double tap makes one order, not two. Nothing here
creates an invoice; the shop reviews first.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import (
    CatalogShareLink,
    CodeSequence,
    CustomerOrder,
    CustomerOrderLine,
    Item,
    Party,
    Tenant,
)
from app.models._mixins import PartyRole, PartySource
from app.reference import validate_phone
from app.schemas_customer_catalog import CatalogLayout
from app.services import share_links
from app.services.catalog import item_catalogs
from app.services.party_resolution import resolve_party

MAX_LINES = 100
MAX_QTY = 999  # whole packs of one item
DEDUPE_WINDOW = timedelta(minutes=2)
SEQ_PREFIX = "ORD"
STATUS_TEXT = {
    "new": "Received. The shop will confirm shortly.",
    "accepted": "Confirmed by the shop.",
    "invoiced": "Confirmed and invoiced.",
    "rejected": "The shop could not accept this order.",
}


def display_number(n: int) -> str:
    return f"ORD-{n:04d}"


@dataclass
class LineIn:
    item_id: str
    qty: int


@dataclass
class Placed:
    order: CustomerOrder
    duplicate: bool = False


def _next_number(session: Session, tenant_id: str) -> int:
    """The next order number for this shop (row-locked, so two orders at once never share one)."""
    stmt = (
        select(CodeSequence)
        .where(CodeSequence.tenant_id == tenant_id, CodeSequence.prefix == SEQ_PREFIX)
        .with_for_update()
    )
    row = session.scalar(stmt)
    if row is None:
        try:
            with session.begin_nested():
                session.add(CodeSequence(tenant_id=tenant_id, prefix=SEQ_PREFIX, next_value=1))
        except IntegrityError:
            pass
        row = session.scalar(stmt)
    assert row is not None
    n = int(row.next_value)
    row.next_value = n + 1
    session.flush()
    return n


def _money(v: object) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def clean_phone(raw: str) -> str:
    try:
        phone = validate_phone(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter a valid phone number.") from exc
    if not phone:
        raise HTTPException(status_code=422, detail="Enter your phone number.")
    return phone


def place(
    session: Session,
    link: CatalogShareLink,
    *,
    name: str,
    phone: str,
    firm: str | None,
    note: str | None,
    wa_opt_in: bool,
    lines: list[LineIn],
) -> Placed:
    tenant = session.get(Tenant, link.tenant_id)
    assert tenant is not None
    name = " ".join(name.split())
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Enter your name.")
    phone = clean_phone(phone)

    # one line per item; whole packs only
    merged: dict[str, int] = {}
    for ln in lines:
        if ln.qty < 1 or ln.qty > MAX_QTY:
            raise HTTPException(
                status_code=422, detail=f"Quantities must be from 1 to {MAX_QTY} packs."
            )
        merged[ln.item_id] = min(MAX_QTY, merged.get(ln.item_id, 0) + ln.qty)
    if not merged:
        raise HTTPException(status_code=422, detail="Your order is empty.")
    if len(merged) > MAX_LINES:
        raise HTTPException(status_code=422, detail=f"At most {MAX_LINES} different items.")

    # a double tap, or a retry after a slow connection, returns the order already made
    digest = hashlib.sha256(
        (phone + "|" + ",".join(f"{k}:{v}" for k, v in sorted(merged.items()))).encode()
    ).hexdigest()
    cutoff = datetime.now(UTC) - DEDUPE_WINDOW
    prior = session.scalar(
        select(CustomerOrder).where(
            CustomerOrder.tenant_id == link.tenant_id,
            CustomerOrder.dedupe_hash == digest,
            CustomerOrder.created_at >= cutoff,
        )
    )
    if prior is not None:
        return Placed(prior, duplicate=True)

    # only what the link offers right now, at the item's own price
    ids = item_catalogs.resolve_selection(session, link.tenant_id, share_links.selection_of(link))
    plan = item_catalogs.plan(
        session, link.tenant_id, ids, CatalogLayout(include_expected=link.include_expected)
    )
    offered: dict[str, Item] = {it.id: it for it in plan.items}
    gone = [i for i in merged if i not in offered]
    if gone:
        names = {
            n_id: n
            for n_id, n in session.execute(
                select(Item.id, Item.name).where(
                    Item.tenant_id == link.tenant_id, Item.id.in_(gone)
                )
            )
        }
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Some items are no longer available. Please check your order.",
                "unavailable": [{"id": i, "name": names.get(i)} for i in gone],
            },
        )

    total = Decimal("0.00")
    built: list[CustomerOrderLine] = []
    for item_id, qty in merged.items():
        it = offered[item_id]
        rate = _money(it.default_rate)
        amount = _money(rate * qty)
        total += amount
        built.append(
            CustomerOrderLine(
                item_id=it.id,
                name=it.name[:300],
                code=(it.sku or it.barcode or None),
                qty=qty,
                pack_qty=int(it.pack_qty or 1),
                rate=rate,
                amount=amount,
            )
        )
    if tenant.order_min_value and total < _money(tenant.order_min_value):
        raise HTTPException(
            status_code=422,
            detail=f"The minimum order is ₹{_money(tenant.order_min_value):,.2f}. "
            f"Your order is ₹{total:,.2f}.",
        )

    match = resolve_party(session, link.tenant_id, name, phone=phone)
    order = CustomerOrder(
        tenant_id=link.tenant_id,
        link_id=link.id,
        number=_next_number(session, link.tenant_id),
        status="new",
        status_token=secrets.token_urlsafe(12),
        customer_name=name[:100],
        customer_phone=phone,
        customer_firm=((firm or "").strip()[:120] or None),
        note=((note or "").strip()[:500] or None),
        wa_opt_in=wa_opt_in,
        # only a phone match is trusted enough to suggest; the shop confirms it on review
        matched_party_id=match.party_id if match.method == "phone" else None,
        item_count=len(built),
        total=total,
        dedupe_hash=digest,
    )
    session.add(order)
    session.flush()
    for ln in built:
        ln.order_id = order.id
        session.add(ln)
    session.flush()
    return Placed(order)


def lines_of(session: Session, order_id: str) -> list[CustomerOrderLine]:
    return list(
        session.scalars(
            select(CustomerOrderLine)
            .where(CustomerOrderLine.order_id == order_id)
            .order_by(CustomerOrderLine.name, CustomerOrderLine.id)
        )
    )


# --------------------------------------------------------------------------- the shop's side (O3)

EDITABLE = ("new", "accepted")


def _check_open(order: CustomerOrder) -> None:
    if order.status not in EDITABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This order is {order.status} and can no longer be changed.",
        )
    if order.invoice_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A draft invoice was already made from this order. Change it there.",
        )


def edit_lines(session: Session, order: CustomerOrder, wanted: dict[str, int]) -> None:
    """Set the packs of each item on the order (0 removes it); an item not on it yet is added at
    its current price. The rate of a line that stays is the rate the customer agreed to."""
    _check_open(order)
    current = {ln.item_id: ln for ln in lines_of(session, order.id) if ln.item_id}
    for item_id, qty in wanted.items():
        if qty < 0 or qty > MAX_QTY:
            raise HTTPException(
                status_code=422, detail=f"Quantities must be from 0 to {MAX_QTY} packs."
            )
        line = current.get(item_id)
        if line is not None:
            if qty == 0:
                session.delete(line)
            else:
                line.qty = qty
                line.amount = _money(line.rate * qty)
        elif qty > 0:
            it = session.scalar(
                select(Item).where(Item.id == item_id, Item.tenant_id == order.tenant_id)
            )
            if it is None or it.default_rate is None or Decimal(str(it.default_rate)) <= 0:
                raise HTTPException(status_code=422, detail="That item cannot be added.")
            rate = _money(it.default_rate)
            session.add(
                CustomerOrderLine(
                    order_id=order.id,
                    item_id=it.id,
                    name=it.name[:300],
                    code=(it.sku or it.barcode or None),
                    qty=qty,
                    pack_qty=int(it.pack_qty or 1),
                    rate=rate,
                    amount=_money(rate * qty),
                )
            )
    session.flush()
    left = lines_of(session, order.id)
    if not left:
        raise HTTPException(
            status_code=422, detail="An order needs at least one item. Reject it instead."
        )
    order.item_count = len(left)
    order.total = sum((ln.amount for ln in left), Decimal("0.00"))
    session.flush()


def set_status(
    session: Session, order: CustomerOrder, new: str, *, reason: str | None = None
) -> None:
    if new == "accepted":
        if order.status != "new":
            raise HTTPException(status_code=409, detail="Only a new order can be accepted.")
    elif new == "rejected":
        _check_open(order)
        if not (reason or "").strip():
            raise HTTPException(status_code=422, detail="Say why, so the customer is told.")
        order.reject_reason = reason.strip()[:1000]  # type: ignore[union-attr]
    order.status = new
    session.flush()


def link_customer(
    session: Session, order: CustomerOrder, *, party_id: str | None, create_new: bool
) -> Party:
    """Say who this customer is: an existing party, or a new one made from the order."""
    _check_open(order)
    if party_id:
        party = session.scalar(
            select(Party).where(Party.id == party_id, Party.tenant_id == order.tenant_id)
        )
        if party is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        if party.status == "archived":
            raise HTTPException(status_code=422, detail=f"{party.legal_name} is archived.")
    elif create_new:
        match = resolve_party(
            session,
            order.tenant_id,
            order.customer_firm or order.customer_name,
            phone=order.customer_phone,
        )
        if match.method in ("phone", "exact", "gstin"):
            existing = session.get(Party, match.party_id)
            raise HTTPException(
                status_code=409,
                detail=f"{existing.legal_name if existing else 'A customer'} already has this "
                "phone number or name. Use that customer.",
            )
        name = (order.customer_firm or order.customer_name).strip()
        party = Party(
            tenant_id=order.tenant_id,
            legal_name=name[:200],
            legal_name_normalized=normalize_name(name, load_synonym_map(session, order.tenant_id)),
            phone=order.customer_phone,
            role=PartyRole.customer,
            source=PartySource.manual,
        )
        session.add(party)
        session.flush()
    else:
        raise HTTPException(status_code=422, detail="Choose a customer or add a new one.")
    order.matched_party_id = party.id
    session.flush()
    return party


def invoice_lines(session: Session, order: CustomerOrder) -> list[dict]:
    """The order as invoice rows: whole packs at the agreed pack rate."""
    out: list[dict] = []
    items = {
        it.id: it
        for it in session.scalars(
            select(Item).where(
                Item.id.in_([ln.item_id for ln in lines_of(session, order.id) if ln.item_id])
            )
        )
    }
    for ln in lines_of(session, order.id):
        it = items.get(ln.item_id or "")
        pack = f" (pack of {ln.pack_qty})" if ln.pack_qty and ln.pack_qty > 1 else ""
        out.append(
            {
                "item_id": ln.item_id if it is not None else None,
                "description": f"{ln.name}{pack}"[:300],
                "hsn_code": it.hsn_code if it is not None else None,
                "quantity": Decimal(ln.qty),
                "uom": it.uom if it is not None else None,
                "unit_rate": Decimal(str(ln.rate)),
            }
        )
    return out

"""Customer orders: placed on the public page (no login), read by the shop.

The shop reviews and moves orders along from O3; here an order is created and can be seen.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import SessionDep
from app.models import CustomerOrder, Invoice, Item, Party, Tenant, WhatsappMessage
from app.routers.catalog import CatalogUser, CatalogWriteUser
from app.routers.invoices import create_invoice
from app.schemas_invoice import InvoiceCreate, InvoiceLineIn
from app.services import audit, order_notify, share_links
from app.services import orders as svc
from app.services.ratelimit import RateLimiter, client_key, limit_dependency

router = APIRouter(prefix="/api/orders", tags=["orders"])
public_router = APIRouter(prefix="/api/public", tags=["public"])

# Placing an order is rare and costly to fake: a few per hour from one address or one phone.
_per_ip = RateLimiter(limit=10, window_seconds=3600)
_per_phone = RateLimiter(limit=5, window_seconds=3600)
_status_limiter = RateLimiter(limit=60, window_seconds=60)
status_limit = Depends(limit_dependency(_status_limiter))


class LineBody(BaseModel):
    item_id: str = Field(min_length=1, max_length=36)
    qty: int = Field(ge=1, le=svc.MAX_QTY)


class OrderBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=30)
    firm: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    wa_opt_in: bool = False
    lines: list[LineBody] = Field(min_length=1, max_length=svc.MAX_LINES)
    # a field real people never see or fill: bots do
    website: str | None = Field(default=None, max_length=200)


class PlacedOut(BaseModel):
    number: str
    status_token: str
    total: str
    item_count: int
    already_placed: bool = False


class PublicLine(BaseModel):
    name: str
    code: str | None
    qty: int
    pack_qty: int
    rate: str
    amount: str


class PublicOrder(BaseModel):
    number: str
    status: str
    status_text: str
    total: str
    placed_at: datetime
    shop_name: str
    shop_phone: str | None
    reject_reason: str | None
    lines: list[PublicLine]


def _m(v: object) -> str:
    return f"{Decimal(str(v)).quantize(Decimal('0.01')):.2f}"


@public_router.post(
    "/catalog/{token}/orders", response_model=PlacedOut, status_code=status.HTTP_201_CREATED
)
def place_order(
    token: str,
    body: OrderBody,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
) -> PlacedOut:
    link = share_links.find_live(session, token)
    if link is None:
        raise HTTPException(status_code=404, detail="This link is not available.")
    if body.website:  # a bot filled the hidden field
        raise HTTPException(status_code=400, detail="Could not place the order.")
    if not _per_ip.check(client_key(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many orders from here. Please try again later or call the shop.",
        )
    phone = svc.clean_phone(body.phone)
    if not _per_phone.check(phone):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many orders for this number. Please call the shop.",
        )
    placed = svc.place(
        session,
        link,
        name=body.name,
        phone=phone,
        firm=body.firm,
        note=body.note,
        wa_opt_in=body.wa_opt_in,
        lines=[svc.LineIn(ln.item_id, ln.qty) for ln in body.lines],
    )
    o = placed.order
    if not placed.duplicate:
        session.commit()  # the background task reads this order from its own session
        background.add_task(order_notify.notify, o.id, "placed")
    return PlacedOut(
        number=svc.display_number(o.number),
        status_token=o.status_token,
        total=_m(o.total),
        item_count=o.item_count,
        already_placed=placed.duplicate,
    )


@public_router.get(
    "/orders/{status_token}", response_model=PublicOrder, dependencies=[status_limit]
)
def public_order(status_token: str, session: SessionDep) -> PublicOrder:
    """What a customer sees of their own order. The token in the address is the key."""
    o = (
        session.scalar(select(CustomerOrder).where(CustomerOrder.status_token == status_token))
        if 0 < len(status_token) <= 40
        else None
    )
    if o is None:
        raise HTTPException(status_code=404, detail="This order was not found.")
    tenant = session.get(Tenant, o.tenant_id)
    assert tenant is not None
    return PublicOrder(
        number=svc.display_number(o.number),
        status=o.status,
        status_text=svc.STATUS_TEXT.get(o.status, o.status),
        total=_m(o.total),
        placed_at=o.created_at,
        shop_name=tenant.trade_name or tenant.legal_name,
        shop_phone=tenant.phone,
        reject_reason=o.reject_reason if o.status == "rejected" else None,
        lines=[
            PublicLine(
                name=ln.name,
                code=ln.code,
                qty=ln.qty,
                pack_qty=ln.pack_qty,
                rate=_m(ln.rate),
                amount=_m(ln.amount),
            )
            for ln in svc.lines_of(session, o.id)
        ],
    )


# --------------------------------------------------------------------------
# the shop's side (same gate as the catalog module)
# --------------------------------------------------------------------------


class OrderLineOut(PublicLine):
    id: str
    item_id: str | None
    # review hints: what is true of the item right now
    available: bool = True
    current_rate: str | None = None


class OrderMessage(BaseModel):
    template: str
    event: str | None
    to_phone: str
    status: str
    error: str | None
    sent_at: datetime | None


class OrderOut(BaseModel):
    id: str
    number: str
    status: str
    customer_name: str
    customer_phone: str
    customer_firm: str | None
    note: str | None
    wa_opt_in: bool
    matched_party_id: str | None
    matched_party_name: str | None
    item_count: int
    total: str
    reject_reason: str | None
    invoice_id: str | None
    link_title: str | None
    created_at: datetime
    invoice_status: str | None = None
    # the WhatsApp messages sent about this order, so a failure is visible
    messages: list[OrderMessage] = []
    lines: list[OrderLineOut] = []


def _order_out(session: SessionDep, o: CustomerOrder, *, with_lines: bool) -> OrderOut:
    from app.models import CatalogShareLink

    party = session.get(Party, o.matched_party_id) if o.matched_party_id else None
    link = session.get(CatalogShareLink, o.link_id) if o.link_id else None
    rows = svc.lines_of(session, o.id) if with_lines else []
    live: dict[str, Item] = {}
    if rows:
        live = {
            it.id: it
            for it in session.scalars(
                select(Item).where(Item.id.in_([r.item_id for r in rows if r.item_id]))
            )
        }
    lines = []
    for ln in rows:
        it = live.get(ln.item_id or "")
        lines.append(
            OrderLineOut(
                id=ln.id,
                item_id=ln.item_id,
                name=ln.name,
                code=ln.code,
                qty=ln.qty,
                pack_qty=ln.pack_qty,
                rate=_m(ln.rate),
                amount=_m(ln.amount),
                available=it is not None and str(it.availability) == "in_stock",
                current_rate=_m(it.default_rate) if it is not None and it.default_rate else None,
            )
        )
    invoice = session.get(Invoice, o.invoice_id) if o.invoice_id else None
    msgs = (
        list(
            session.scalars(
                select(WhatsappMessage)
                .where(WhatsappMessage.order_id == o.id)
                .order_by(WhatsappMessage.created_at, WhatsappMessage.id)
            )
        )
        if with_lines
        else []
    )
    return OrderOut(
        id=o.id,
        number=svc.display_number(o.number),
        status=o.status,
        customer_name=o.customer_name,
        customer_phone=o.customer_phone,
        customer_firm=o.customer_firm,
        note=o.note,
        wa_opt_in=o.wa_opt_in,
        matched_party_id=o.matched_party_id,
        matched_party_name=party.legal_name if party else None,
        item_count=o.item_count,
        total=_m(o.total),
        reject_reason=o.reject_reason,
        invoice_id=o.invoice_id,
        link_title=link.title if link else None,
        created_at=o.created_at,
        invoice_status=str(invoice.status) if invoice is not None else None,
        messages=[
            OrderMessage(
                template=m.template_name,
                event=m.order_event,
                to_phone=m.to_phone,
                status=m.status,
                error=m.error,
                sent_at=m.sent_at,
            )
            for m in msgs
        ],
        lines=lines,
    )


@router.get("", response_model=list[OrderOut])
def list_orders(
    session: SessionDep,
    user: CatalogUser,
    status_: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[OrderOut]:
    stmt = select(CustomerOrder).where(CustomerOrder.tenant_id == user.tenant_id)
    if status_:
        stmt = stmt.where(CustomerOrder.status == status_)
    rows = session.scalars(stmt.order_by(CustomerOrder.number.desc()).limit(limit))
    return [_order_out(session, o, with_lines=False) for o in rows]


@router.get("/counts")
def order_counts(session: SessionDep, user: CatalogUser) -> dict[str, int]:
    """How many orders are in each status (the badge on the Orders tab is `new`)."""
    from sqlalchemy import func

    rows = session.execute(
        select(CustomerOrder.status, func.count())
        .where(CustomerOrder.tenant_id == user.tenant_id)
        .group_by(CustomerOrder.status)
    ).all()
    return {s: int(n) for s, n in rows}


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: str, session: SessionDep, user: CatalogUser) -> OrderOut:
    o = session.scalar(
        select(CustomerOrder).where(
            CustomerOrder.id == order_id, CustomerOrder.tenant_id == user.tenant_id
        )
    )
    if o is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _order_out(session, o, with_lines=True)


class EditLine(BaseModel):
    item_id: str = Field(min_length=1, max_length=36)
    qty: int = Field(ge=0, le=svc.MAX_QTY)


class LinesBody(BaseModel):
    """Packs wanted per item; 0 removes the item, an item not on the order yet is added."""

    lines: list[EditLine] = Field(min_length=1, max_length=svc.MAX_LINES)

    model_config = {"extra": "forbid"}


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class CustomerBody(BaseModel):
    party_id: str | None = None
    create_new: bool = False


def _owned(session: SessionDep, tenant_id: str, order_id: str) -> CustomerOrder:
    o = session.scalar(
        select(CustomerOrder).where(
            CustomerOrder.id == order_id, CustomerOrder.tenant_id == tenant_id
        )
    )
    if o is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return o


def _audit(session: SessionDep, user, o: CustomerOrder, action: str, **after: object) -> None:  # type: ignore[no-untyped-def]
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="customer_order",
        entity_id=o.id,
        action=action,
        after={"number": svc.display_number(o.number), **after},
    )


@router.put("/{order_id}/lines", response_model=OrderOut)
def edit_order(
    order_id: str, body: LinesBody, session: SessionDep, user: CatalogWriteUser
) -> OrderOut:
    o = _owned(session, user.tenant_id, order_id)
    svc.edit_lines(session, o, {ln.item_id: ln.qty for ln in body.lines})
    _audit(session, user, o, "edit", items=o.item_count, total=_m(o.total))
    return _order_out(session, o, with_lines=True)


@router.post("/{order_id}/accept", response_model=OrderOut)
def accept_order(
    order_id: str, background: BackgroundTasks, session: SessionDep, user: CatalogWriteUser
) -> OrderOut:
    o = _owned(session, user.tenant_id, order_id)
    svc.set_status(session, o, "accepted")
    _audit(session, user, o, "accept")
    session.commit()
    background.add_task(order_notify.notify, o.id, "accepted")
    return _order_out(session, o, with_lines=True)


@router.post("/{order_id}/reject", response_model=OrderOut)
def reject_order(
    order_id: str,
    body: RejectBody,
    background: BackgroundTasks,
    session: SessionDep,
    user: CatalogWriteUser,
) -> OrderOut:
    o = _owned(session, user.tenant_id, order_id)
    svc.set_status(session, o, "rejected", reason=body.reason)
    _audit(session, user, o, "reject", reason=o.reject_reason)
    session.commit()
    background.add_task(order_notify.notify, o.id, "rejected")
    return _order_out(session, o, with_lines=True)


@router.post("/{order_id}/customer", response_model=OrderOut)
def set_customer(
    order_id: str, body: CustomerBody, session: SessionDep, user: CatalogWriteUser
) -> OrderOut:
    """Confirm who the customer is: an existing customer, or a new one made from the order."""
    o = _owned(session, user.tenant_id, order_id)
    party = svc.link_customer(session, o, party_id=body.party_id, create_new=body.create_new)
    _audit(session, user, o, "set_customer", party=party.legal_name, created=body.create_new)
    return _order_out(session, o, with_lines=True)


@router.post("/{order_id}/invoice", response_model=OrderOut)
def make_invoice(order_id: str, session: SessionDep, user: CatalogWriteUser) -> OrderOut:
    """Make a DRAFT sales invoice from this order. The shop finalizes it as usual: nothing is
    numbered here, and the order is marked invoiced only when the invoice is finalized."""
    o = _owned(session, user.tenant_id, order_id)
    if o.invoice_id:
        raise HTTPException(
            status_code=409, detail="A draft invoice was already made from this order."
        )
    if o.status not in svc.EDITABLE:
        raise HTTPException(status_code=409, detail=f"This order is {o.status}.")
    if not o.matched_party_id:
        raise HTTPException(
            status_code=422, detail="Confirm who the customer is before making the invoice."
        )
    rows = svc.invoice_lines(session, o)
    note = f"Order {svc.display_number(o.number)}" + (f". {o.note}" if o.note else "")
    inv = create_invoice(
        InvoiceCreate(
            party_id=o.matched_party_id,
            notes=note[:2000],
            lines=[InvoiceLineIn(**r) for r in rows],
        ),
        user,
        session,
    )
    o.invoice_id = inv.id
    if o.status == "new":
        o.status = "accepted"
    _audit(session, user, o, "make_invoice", invoice=inv.id)
    session.flush()
    return _order_out(session, o, with_lines=True)

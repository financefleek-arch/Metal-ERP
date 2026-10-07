"""WhatsApp messages about customer orders.

Events: `placed` (the customer's "order received" and the shop's alert), `accepted`, `rejected`
and `invoiced` (the customer's updates). Rules:

  - best effort and never in the way: this runs after the request is answered, in its own session,
    and never raises; a failed or skipped message is recorded, not thrown;
  - the customer is only messaged if they ticked the WhatsApp box on the order page;
  - the shop is only alerted if it set an alert number (it cannot message its own sending number);
  - each (order, event, audience) is sent once, so a retry or a second call is harmless.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CustomerOrder, Invoice, Tenant, WhatsappMessage
from app.services import whatsapp as wa
from app.services.orders import display_number

log = logging.getLogger("orders.notify")

CUSTOMER = "order_received"
UPDATE = "order_update"
ALERT = "new_order_alert"


def _money(v: object) -> str:
    return f"{Decimal(str(v)).quantize(Decimal('0.01')):.2f}"


def _already(session, order_id: str, event: str, template: str) -> bool:  # type: ignore[no-untyped-def]
    return (
        session.scalar(
            select(WhatsappMessage.id).where(
                WhatsappMessage.order_id == order_id,
                WhatsappMessage.order_event == event,
                WhatsappMessage.template_name == template,
                WhatsappMessage.status != "failed",
            )
        )
        is not None
    )


def _update_text(session, order: CustomerOrder, event: str) -> tuple[str, str] | None:  # type: ignore[no-untyped-def]
    """(status word, detail) for an update message."""
    if event == "accepted":
        return "confirmed", "We will send your invoice shortly."
    if event == "rejected":
        # the approved text reads "Your order is now {{3}}. {{4}} If you have any questions...",
        # so the status must fit after "is now" and the detail must be a finished sentence
        reason = " ".join((order.reject_reason or "").split()).rstrip(".!? ")
        if not reason:
            return "cancelled", "Please call the shop for details."
        return "cancelled", f"Reason: {reason[0].upper()}{reason[1:]}."
    if event == "invoiced":
        inv = session.get(Invoice, order.invoice_id) if order.invoice_id else None
        if inv is None or inv.number is None:
            return None
        return (
            "invoiced",
            f"Invoice {inv.number} for ₹{_money(inv.grand_total or order.total)} is ready.",
        )
    return None


def notify(order_id: str, event: str) -> None:
    """Send what this event calls for. Safe to call from a background task."""
    try:
        with SessionLocal() as session:
            order = session.get(CustomerOrder, order_id)
            tenant = session.get(Tenant, order.tenant_id) if order else None
            if order is None or tenant is None:
                return
            number = display_number(order.number)
            shop = tenant.trade_name or tenant.legal_name
            sent: list[tuple[str, str]] = []

            def send(template: str, to: str, values: dict[str, str], button: str | None) -> None:
                if _already(session, order.id, event, template):
                    return
                try:
                    wa.send_order_message(
                        session,
                        tenant_id=order.tenant_id,
                        order_id=order.id,
                        event=event,
                        template_name=template,
                        to_phone=to,
                        values=values,
                        button_url_param=button,
                        party_id=order.matched_party_id if template != ALERT else None,
                    )
                    sent.append((template, "sent"))
                except wa.WhatsappError as exc:
                    # not configured, or Meta said no: the failed row (if any) shows why
                    log.info("order %s %s: %s not sent: %s", number, event, template, exc)
                    sent.append((template, "failed"))
                session.commit()

            if event == "placed":
                if order.wa_opt_in:
                    send(
                        CUSTOMER,
                        order.customer_phone,
                        {
                            "customer_name": order.customer_name.split()[0],
                            "shop_name": shop,
                            "order_number": number,
                            "item_count": str(order.item_count),
                            "total": _money(order.total),
                        },
                        order.status_token,
                    )
                if tenant.order_alert_phone:
                    send(
                        ALERT,
                        tenant.order_alert_phone,
                        {
                            "order_number": number,
                            "customer_name": order.customer_name,
                            "item_count": str(order.item_count),
                            "total": _money(order.total),
                        },
                        None,
                    )
            elif order.wa_opt_in:
                text = _update_text(session, order, event)
                if text is not None:
                    send(
                        UPDATE,
                        order.customer_phone,
                        {
                            "customer_name": order.customer_name.split()[0],
                            "order_number": number,
                            "status_word": text[0],
                            "detail": text[1],
                        },
                        order.status_token,  # the approved template's "View Order" button
                    )
    except Exception:  # noqa: BLE001 - a notification must never break the order flow
        log.exception("order notification failed (%s, %s)", order_id, event)

"""The Setup screen: what a new firm should do first, and what else it can switch on.

Everything here is worked out from the firm's real data and its plan, never ticked by hand, so the
checklist cannot drift from the truth. Part A is the Free core (get invoicing going); Part B is one
card per optional feature with a short guide and the firm's status for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BackupShop,
    Invoice,
    Item,
    Party,
    Payment,
    Tenant,
    TenantWhatsappConfig,
)
from app.models._mixins import InvoiceStatus
from app.services import entitlements, features


def _count(session: Session, model: Any, tenant_id: str, *extra: Any) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *extra)
        )
        or 0
    )


def steps(session: Session, tenant: Tenant) -> list[dict[str, Any]]:
    """Part A. Each step is done when the data it asks for exists."""
    tid = tenant.id
    rows = [
        (
            "firm",
            "Confirm your firm details",
            "Address and state print on every invoice.",
            bool(tenant.address and tenant.state_code),
            "/firm",
        ),
        (
            "bank",
            "Add your bank or UPI and terms",
            "Printed at the bottom of the invoice so customers know how to pay you.",
            bool(tenant.bank_ac_no or tenant.upi_id),
            "/firm",
        ),
        (
            "party",
            "Add your first customer",
            "A name and a phone number is enough to start.",
            _count(session, Party, tid) > 0,
            "/parties/new",
        ),
        (
            "item",
            "Add your first item",
            "Your item groups are already set up; add the products you sell.",
            _count(session, Item, tid) > 0,
            "/items/new",
        ),
        (
            "invoice",
            "Make your first invoice",
            "Finalise it to get the PDF, then share it from your phone.",
            _count(session, Invoice, tid, Invoice.status == InvoiceStatus.final) > 0,
            "/invoices/new",
        ),
        (
            "payment",
            "Record a payment",
            "Open a customer and record what they paid; their balance and ledger update.",
            _count(session, Payment, tid) > 0,
            "/parties",
        ),
    ]
    return [
        {"key": k, "title": t, "hint": h, "done": d, "route": r} for k, t, h, d, r in rows
    ]


# key, title, what it does, who needs it, what to have ready, minutes, in-app route, plan features it needs
_CATALOGUE: list[dict[str, Any]] = [
    {
        "key": "whatsapp",
        "title": "Send from your own WhatsApp number",
        "summary": (
            "Invoices, statements and reminders go to your customers from your shop's number, "
            "with the PDF attached."
        ),
        "who": "Shops that send bills to customers every day.",
        "ready": [
            "A phone number that is on the WhatsApp Business app, or a new number",
            "The Facebook login of the person who manages your business",
            "A card or UPI for Meta's own message charges",
        ],
        "minutes": 15,
        "route": "/firm",
        "needs": ["whatsapp"],
    },
    {
        "key": "reminders",
        "title": "Collections and payment reminders",
        "summary": (
            "See who owes you and for how long. Reminders are proposed every day; "
            "you approve before they go."
        ),
        "who": "Shops that sell on credit.",
        "ready": ["Your customers' phone numbers", "WhatsApp connected (above)"],
        "minutes": 5,
        "route": "/collections",
        "needs": ["collections", "reminders"],
    },
    {
        "key": "import",
        "title": "Bulk import of customers and items",
        "summary": "Bring your lists in from Excel or Tally instead of typing them.",
        "who": "Shops with a large item or customer list already on file.",
        "ready": ["An Excel sheet or a Tally export of your parties or items"],
        "minutes": 10,
        "route": "/parties/import",
        "needs": ["import"],
    },
    {
        "key": "capture",
        "title": "Photo slip and voice entry",
        "summary": (
            "Photograph a handwritten slip or speak the items and weights. "
            "You always confirm before it is saved."
        ),
        "who": "Counters that write bills by hand first.",
        "ready": ["A phone with a camera or microphone"],
        "minutes": 2,
        "route": "/invoices/new",
        "needs": ["slip", "voice"],
    },
    {
        "key": "catalogs",
        "title": "Customer catalogs and online ordering",
        "summary": (
            "Send each customer a link to your price list; they order from their phone "
            "and you get the order here."
        ),
        "who": "Shops that take repeat orders from fixed customers.",
        "ready": ["Items with prices and photos", "WhatsApp connected"],
        "minutes": 20,
        "route": "/items/catalogs",
        "ext": "ext_supplier_catalog",
        "needs": [],
    },
    {
        "key": "inward",
        "title": "Purchase bills",
        "summary": "Upload a supplier's bill and review it before it goes to your books.",
        "who": "Shops that want purchase entries without typing.",
        "ready": ["Supplier bills as PDF or photos"],
        "minutes": 10,
        "route": "/documents",
        "ext": "ext_inward_import",
        "needs": [],
    },
    {
        "key": "tally",
        "title": "Tally connection",
        "summary": "Back up your Tally data and push invoices into Tally, from your PC to this app.",
        "who": "Shops whose accountant works in Tally.",
        "ready": [
            "Tally Prime running on a Windows PC",
            "Permission to install a small helper on that PC",
        ],
        "minutes": 20,
        "route": "/firm",
        "needs": ["tally"],
    },
]


def _requested(tenant: Tenant, key: str) -> str | None:
    return (tenant.feature_requests or {}).get(key)


def _set_up(session: Session, tenant: Tenant, key: str) -> bool:
    """For a feature the firm may use: has it finished setting it up."""
    tid = tenant.id
    if key == "whatsapp":
        cfg = session.scalar(select(TenantWhatsappConfig).where(TenantWhatsappConfig.tenant_id == tid))
        return bool(cfg and cfg.is_active)
    if key == "reminders":
        return bool(tenant.reminder_enabled)
    if key == "tally":
        return _count(session, BackupShop, tid, BackupShop.is_active.is_(True)) > 0
    # catalogs and purchase bills have nothing to finish; import and capture are "just use it"
    return key in ("catalogs", "inward")


def feature_cards(session: Session, tenant: Tenant) -> list[dict[str, Any]]:
    cards = []
    for c in _CATALOGUE:
        ext = c.get("ext")
        allowed = all(features.allowed(session, tenant, k) for k in c["needs"]) and (
            ext is None or bool(getattr(tenant, ext))
        )
        if allowed:
            status = "active" if _set_up(session, tenant, c["key"]) else "setup"
        else:
            status = "requested" if _requested(tenant, c["key"]) else "locked"
        cards.append(
            {
                "key": c["key"],
                "title": c["title"],
                "summary": c["summary"],
                "who": c["who"],
                "ready": c["ready"],
                "minutes": c["minutes"],
                "route": c["route"],
                "status": status,
                "requested_at": _requested(tenant, c["key"]),
            }
        )
    return cards


def feature_keys() -> set[str]:
    return {c["key"] for c in _CATALOGUE}


def overview(session: Session, tenant: Tenant) -> dict[str, Any]:
    st = steps(session, tenant)
    licence = entitlements.current(session, tenant)
    return {
        "dismissed": tenant.onboarding_dismissed_at is not None,
        "steps": st,
        "done": sum(1 for s in st if s["done"]),
        "total": len(st),
        "features": feature_cards(session, tenant),
        "plan_tier": "free" if features.is_free(session, tenant) else "full",
        "plan_label": licence.plan_label if licence else None,
    }


def mark_requested(tenant: Tenant, key: str) -> str:
    stamp = datetime.now(UTC).isoformat()
    tenant.feature_requests = {**(tenant.feature_requests or {}), key: stamp}
    return stamp

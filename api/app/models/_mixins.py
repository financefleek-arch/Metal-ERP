"""Shared column mixins and enums."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column


def _uuid_str() -> str:
    return str(uuid.uuid4())


class PkUuidMixin:
    """String UUID primary key — portable, no DB extension needed."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --- enums (stored as native strings, not PG ENUM, for painless additions) ---


class UserRole(enum.StrEnum):
    owner = "owner"
    accountant = "accountant"
    viewer = "viewer"
    # Stage 1+ touchpoint roles — defined now, unused until then.
    counter = "counter"
    weighbridge = "weighbridge"
    rate_desk = "rate_desk"


class PartyRole(enum.StrEnum):
    customer = "customer"
    supplier = "supplier"
    both = "both"


class PartyStatus(enum.StrEnum):
    active = "active"
    archived = "archived"


class WaConsent(enum.StrEnum):
    """Whether we may send this party price lists / catalogs (marketing) on WhatsApp."""

    none = "none"  # never asked: do not send
    opted_in = "opted_in"
    opted_out = "opted_out"  # said no or STOP: never send again until they opt in


class WaConsentSource(enum.StrEnum):
    manual = "manual"  # staff recorded a verbal / in-person yes
    order_page = "order_page"  # ticked the box on the customer order page
    whatsapp_reply = "whatsapp_reply"  # replied YES / STOP in the chat


class PartySource(enum.StrEnum):
    manual = "manual"
    inward_bill = "inward_bill"
    tally_import = "tally_import"
    catalog = "catalog"


class AddressType(enum.StrEnum):
    bill = "bill"
    ship = "ship"
    both = "both"


class ItemType(enum.StrEnum):
    bulk = "bulk"
    mrp = "mrp"


class Availability(enum.StrEnum):
    """Whether the firm can offer an item. A status, not a quantity: Tally keeps the stock."""

    in_stock = "in_stock"
    expected = "expected"
    out_of_stock = "out_of_stock"
    discontinued = "discontinued"


class ItemSource(enum.StrEnum):
    manual = "manual"
    auto_from_invoice = "auto_from_invoice"
    auto_from_purchase = "auto_from_purchase"
    import_ = "import"
    catalog = "catalog"


class ItemStatus(enum.StrEnum):
    unconfirmed = "unconfirmed"
    confirmed = "confirmed"
    archived = "archived"


class AliasSource(enum.StrEnum):
    manual = "manual"
    # off a real inward document — never auto-retired
    auto_from_purchase = "auto_from_purchase"
    # explicit "create new" on a sales line — never auto-retired
    auto_from_invoice = "auto_from_invoice"
    # from the billing type-ahead — swept if unused for 90 days
    learned = "learned"


class DocType(enum.StrEnum):
    inv = "inv"
    crn = "crn"  # credit note
    dbn = "dbn"  # debit note


class InvoiceStatus(enum.StrEnum):
    draft = "draft"
    final = "final"
    cancelled = "cancelled"


class PdfStatus(enum.StrEnum):
    none = "none"
    rendered = "rendered"
    failed = "failed"


# --- inward bill import (ext_inward_import) ---


class InwardStatus(enum.StrEnum):
    uploaded = "uploaded"
    extracting = "extracting"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"
    error = "error"


class ExtractionMethod(enum.StrEnum):
    einvoice_qr = "einvoice_qr"
    template = "template"
    table = "table"
    vision_llm = "vision_llm"


class MatchMethod(enum.StrEnum):
    exact = "exact"
    alias = "alias"
    fuzzy = "fuzzy"
    llm = "llm"
    code = "code"  # the supplier's own code on the line, or the supplier's product name
    new = "new"
    manual = "manual"


class SupplyType(enum.StrEnum):
    intra = "intra"  # supplier state == buyer state → CGST + SGST
    inter = "inter"  # different states → IGST


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


# --- payments (party ledger, Tally-shaped for future export) ---


class PaymentMode(enum.StrEnum):
    cash = "cash"
    upi = "upi"
    bank = "bank"
    cheque = "cheque"


class PaymentStatus(enum.StrEnum):
    posted = "posted"
    # a bounced cheque / wrong entry — allocations unwound, balances restored.
    # never deleted outright: keeps the ledger + any future Tally export honest.
    reversed = "reversed"


class AllocationType(enum.StrEnum):
    """Mirrors Tally's Bill-wise Details BILLTYPE: "Agst Ref" vs "New Ref"/On
    Account. against_invoice ties the amount to one invoice's balance; on_account
    is an unapplied credit sitting against the party only."""

    against_invoice = "against_invoice"
    on_account = "on_account"

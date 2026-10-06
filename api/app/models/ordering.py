"""Customer ordering: share links (O1) and orders placed from them (O2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class CatalogShareLink(PkUuidMixin, TimestampMixin, Base):
    """A public link to a catalog: anyone holding the URL can see it (and, from O2, order).

    The link shows the chosen items **as they are now** (current prices and availability), not a
    snapshot. The token is a capability URL: long, random, revocable, optionally expiring.
    """

    __tablename__ = "catalog_share_link"
    __table_args__ = (UniqueConstraint("token", name="uq_catalog_share_link_token"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    token: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # {"ids": [...]} or {"filter": {...}}; a filter is re-evaluated on every view
    selection_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    # also show items marked Expected, tagged "on order"
    include_expected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerOrder(PkUuidMixin, TimestampMixin, Base):
    """An order a customer placed from a share link. It is a request: nothing is promised until
    the shop reviews it, and it never becomes an invoice by itself (O3 turns it into a draft)."""

    __tablename__ = "customer_order"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_customer_order_tenant_number"),
        UniqueConstraint("status_token", name="uq_customer_order_status_token"),
        Index("ix_customer_order_tenant_status", "tenant_id", "status"),
        Index("ix_customer_order_dedupe", "tenant_id", "dedupe_hash"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    link_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_share_link.id"))
    # shown as ORD-0042; its own counter per shop, separate from invoice numbers
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # new | accepted | invoiced | rejected (the shop moves it along, from O3)
    status: Mapped[str] = mapped_column(String(10), default="new", nullable=False)
    status_token: Mapped[str] = mapped_column(String(40), nullable=False)

    customer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_firm: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(500))
    wa_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # an existing customer whose phone matched (the shop confirms it on review)
    matched_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"))

    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # the draft invoice made from it (O3)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoice.id"))
    # a double tap on "Place order" must not make two orders
    dedupe_hash: Mapped[str | None] = mapped_column(String(64))


class CustomerOrderLine(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "customer_order_line"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("customer_order.id"), nullable=False, index=True
    )
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)
    # what the customer saw and agreed to, kept even if the item changes later
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer, nullable=False)  # whole packs
    pack_qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)  # per pack
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

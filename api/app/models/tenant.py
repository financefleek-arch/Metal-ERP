"""Tenant (the business) and its users.

Single tenant for Milestone 1, but the column and every FK exists so
multi-tenant is not a retrofit.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin, UserRole


class Tenant(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "tenant"

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    pan: Mapped[str | None] = mapped_column(String(10))

    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state_code: Mapped[str | None] = mapped_column(String(2))
    pincode: Mapped[str | None] = mapped_column(String(6))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    logo_url: Mapped[str | None] = mapped_column(String(500))

    # Bank block — printed on the invoice.
    bank_holder: Mapped[str | None] = mapped_column(String(200))
    bank_name: Mapped[str | None] = mapped_column(String(200))
    bank_ac_no: Mapped[str | None] = mapped_column(String(50))
    bank_ifsc: Mapped[str | None] = mapped_column(String(20))
    bank_branch: Mapped[str | None] = mapped_column(String(200))
    upi_id: Mapped[str | None] = mapped_column(String(100))

    # Printed footer text.
    declaration_text: Mapped[str | None] = mapped_column(Text)
    terms_text: Mapped[str | None] = mapped_column(Text)
    jurisdiction_text: Mapped[str | None] = mapped_column(Text)

    document_label: Mapped[str] = mapped_column(String(50), default="Invoice", nullable=False)

    # A party with no transaction inside this window is flagged "dormant"
    # (data + filter only for M1; no dashboard tile yet).
    dormant_party_days: Mapped[int] = mapped_column(Integer, default=180, nullable=False)

    # Days after an invoice's date that its balance is considered "due" —
    # drives the Collections ageing buckets (F3a). 0 = "due on invoice date",
    # which is the pre-column behaviour. Tenant-wide; no per-party override yet.
    default_credit_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Baseline markup for the (future) price-suggestion engine. Null = not set.
    default_markup_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))

    # Phase 2 — dormant.
    gst_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))

    # Extension flag: Inward Bill Import module. Off by default; when false the
    # /api/inward-bills* routes 404 and the "Inward" nav item is hidden.
    # Toggled by a DB update / seed until an admin screen exists.
    ext_inward_import: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Extension flag: Supplier Catalog module (supplier price-list PDF -> priced
    # items, barcode labels, customer catalog, Tally stock items). Off by
    # default; when false the /api/supplier-catalogs* routes 404.
    ext_supplier_catalog: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Default item-code prefix for new catalogs (e.g. "GL"). Null = derive one.
    catalog_code_prefix: Mapped[str | None] = mapped_column(String(8))
    # 'auto' = create our group (item_category) when a catalog item names one
    # that doesn't exist; 'suggest_only' = leave blank until the user approves.
    catalog_group_create_policy: Mapped[str] = mapped_column(
        String(12), default="auto", server_default="auto", nullable=False
    )

    # Customer ordering (one setting for the whole shop): the smallest order value accepted,
    # and one terms line shown on the customer catalog page.
    order_min_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    order_terms_line: Mapped[str | None] = mapped_column(String(300))
    # the shop's own WhatsApp number for new-order alerts (none = no alert)
    order_alert_phone: Mapped[str | None] = mapped_column(String(20))

    # Payment reminders: proposed daily, sent only when the shop approves. Off until switched on.
    reminder_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # True = reminders that have just come due go out without approval (see services/reminders.py)
    reminder_auto_send: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # Fleek's switch for this firm: when off, automatic sending is refused and stops, whatever the
    # shop chose. On by default; the shop still has to opt in with `reminder_auto_send`.
    reminder_auto_allowed: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # days past due at which a reminder becomes due, comma separated ("7,15,30")
    reminder_days: Mapped[str] = mapped_column(
        String(40), default="7,15,30", server_default="7,15,30", nullable=False
    )

    users: Mapped[list[User]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(20), default=UserRole.owner, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Platform operator: may cross tenant boundaries on /api/admin/* only.
    # Set exclusively by tools.make_platform_admin; these users belong to the
    # dedicated "Fleek Operations" tenant and have no real firm data.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="users")

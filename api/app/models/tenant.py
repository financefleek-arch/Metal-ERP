"""Tenant (the business) and its users.

Single tenant for Milestone 1, but the column and every FK exists so
multi-tenant is not a retrofit.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
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

    # Phase 2 — dormant.
    gst_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))

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

    tenant: Mapped[Tenant] = relationship(back_populates="users")

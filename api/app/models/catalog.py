"""Supplier Catalog — supplier price-list PDF -> priced items -> labels,
customer catalog, Tally stock items.

A feature-flagged module (`tenant.ext_supplier_catalog`). Catalog rows live in
their own tables; only *promoted* rows become `Item`s (docs/EXECUTION-PLAN-
supplier-catalog.md). Money is NUMERIC(15,2); multipliers NUMERIC(6,3).
"""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

_MONEY = Numeric(15, 2)
_MULT = Numeric(6, 3)
# JSONB on Postgres, plain JSON on SQLite (tests).
_JSON = JSON().with_variant(JSONB(), "postgresql")


class SupplierCatalog(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "supplier_catalog"
    __table_args__ = (
        # Same file uploaded twice is the same catalog.
        UniqueConstraint("tenant_id", "source_sha256", name="uq_supplier_catalog_tenant_sha"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    supplier_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(300))
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Item-code prefix for this catalog's auto-generated codes (e.g. "GL").
    code_prefix: Mapped[str] = mapped_column(String(8), nullable=False)

    # One multiplier for the whole catalog; per-item override on the item.
    multiplier: Mapped[Decimal] = mapped_column(_MULT, default=Decimal("1.000"), nullable=False)
    rounding_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # extracting | ready | error
    status: Mapped[str] = mapped_column(String(12), default="extracting", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    # S6: a newer upload from the same supplier points at the one it replaces.
    supersedes_catalog_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_catalog.id"))

    items: Mapped[list[SupplierCatalogItem]] = relationship(
        back_populates="catalog",
        cascade="all, delete-orphan",
        order_by="SupplierCatalogItem.page_no, SupplierCatalogItem.position",
    )


class SupplierCatalogItem(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "supplier_catalog_item"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_catalog_item_tenant_code"),
        UniqueConstraint("catalog_id", "supplier_code", name="uq_catalog_item_supplier_code"),
        Index("ix_catalog_item_group", "catalog_id", "category_id"),
        Index("ix_catalog_item_included", "catalog_id", "included"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    catalog_id: Mapped[str] = mapped_column(
        ForeignKey("supplier_catalog.id"), nullable=False, index=True
    )

    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # The supplier's own code (match key for a re-sent catalog) and OUR code.
    supplier_code: Mapped[str] = mapped_column(String(60), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)

    name_raw: Mapped[str] = mapped_column(String(400), nullable=False)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(40))
    size_text: Mapped[str | None] = mapped_column(String(60))

    # Price is per PACK as the supplier quotes it ("Rs 190 for 6 pcs").
    pack_qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    carton_qty: Mapped[int | None] = mapped_column(Integer)
    cost_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    multiplier_override: Mapped[Decimal | None] = mapped_column(_MULT)
    sell_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)

    # Our group = an item_category, find-or-created by name.
    category_id: Mapped[str | None] = mapped_column(ForeignKey("item_category.id"))
    # Policy 'suggest_only': the group the rules proposed, not yet created.
    suggested_group: Mapped[str | None] = mapped_column(String(60))

    image_key: Mapped[str | None] = mapped_column(String(300))
    image_w: Mapped[int | None] = mapped_column(Integer)
    image_h: Mapped[int | None] = mapped_column(Integer)
    image_sha256: Mapped[str | None] = mapped_column(String(64))

    included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Set on promote (S5).
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)
    # none | pushed | failed
    tally_status: Mapped[str] = mapped_column(String(8), default="none", nullable=False)
    tally_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # S6: new | up | down | same | dropped
    price_change: Mapped[str | None] = mapped_column(String(8))

    catalog: Mapped[SupplierCatalog] = relationship(back_populates="items")


class CodeSequence(Base):
    """Next item-code number per (tenant, prefix). Row-locked on allocation;
    numbers are never reused, so a code stays unique even if its item is
    deleted. See `app/services/catalog/codes.py`.
    """

    __tablename__ = "code_sequence"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CatalogOutputJob(PkUuidMixin, TimestampMixin, Base):
    """A generated file too big to build inside one request (label sheets of thousands).

    The request creates the row and a background task fills in `result_key`; the SPA
    polls the row. `params_json` holds what is needed to rebuild the output (item ids in
    print order, preset, options).
    """

    __tablename__ = "catalog_output_job"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    catalog_id: Mapped[str] = mapped_column(
        ForeignKey("supplier_catalog.id"), nullable=False, index=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # 'labels'
    params_json: Mapped[dict | None] = mapped_column(_JSON)
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    result_key: Mapped[str | None] = mapped_column(String(300))
    scan_warning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class CustomerCatalog(PkUuidMixin, TimestampMixin, Base):
    """A generated, versioned customer-facing catalog PDF (the shop's prices).

    A snapshot: it is marked `stale` when the catalog's prices or items change afterwards,
    and the user regenerates to get the next version.
    """

    __tablename__ = "customer_catalog"
    __table_args__ = (
        UniqueConstraint("catalog_id", "version", name="uq_customer_catalog_version"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    catalog_id: Mapped[str] = mapped_column(
        ForeignKey("supplier_catalog.id"), nullable=False, index=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    options_json: Mapped[dict | None] = mapped_column(_JSON)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_key: Mapped[str] = mapped_column(String(300), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

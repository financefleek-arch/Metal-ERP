"""Supplier Catalog — supplier price-list PDF -> priced items -> labels,
customer catalog, Tally stock items.

A feature-flagged module (`tenant.ext_supplier_catalog`). Catalog rows live in
their own tables; only *promoted* rows become `Item`s (docs/EXECUTION-PLAN-
supplier-catalog.md). Money is NUMERIC(15,2); margins NUMERIC(7,2) (percent).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

_MONEY = Numeric(15, 2)
_MARGIN = Numeric(7, 2)  # percent, e.g. 25.00 = +25%
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

    # One bulk margin (%) for the whole catalog; an item-level margin overrides it per item.
    bulk_margin_pct: Mapped[Decimal] = mapped_column(
        _MARGIN, default=Decimal("0.00"), nullable=False
    )
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
        # The same product (same code) may appear in several catalogs, so a code is unique
        # per catalog; it is unique per firm on `catalog_product`.
        UniqueConstraint("catalog_id", "code", name="uq_catalog_item_catalog_code"),
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
    item_margin_pct: Mapped[Decimal | None] = mapped_column(_MARGIN)  # null = bulk margin
    sell_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)

    # The shop's product this row is an offer of: it owns our code, group and name.
    product_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_product.id"), index=True)
    # A different product that looks like the same thing (same normalised name): the user can
    # accept (link) or dismiss it.
    suggested_product_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_product.id"))
    # Our group = an item_category, find-or-created by name.
    category_id: Mapped[str | None] = mapped_column(ForeignKey("item_category.id"))
    # Policy 'suggest_only': the group the rules proposed, not yet created.
    suggested_group: Mapped[str | None] = mapped_column(String(60))

    image_key: Mapped[str | None] = mapped_column(String(300))
    image_w: Mapped[int | None] = mapped_column(Integer)
    image_h: Mapped[int | None] = mapped_column(Integer)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    # small | odd_shape | blank | unreadable: the crop probably needs a look (see image_check)
    image_flag: Mapped[str | None] = mapped_column(String(12))

    included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Set on promote (S5).
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)
    # none | pushed | failed
    tally_status: Mapped[str] = mapped_column(String(8), default="none", nullable=False)
    tally_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # S6: new | up | down | same | dropped
    price_change: Mapped[str | None] = mapped_column(String(8))
    # The selling price last sent to Tally for this row (None = never). A different current
    # price makes the row due for a price update.
    tally_price: Mapped[Decimal | None] = mapped_column(_MONEY)

    catalog: Mapped[SupplierCatalog] = relationship(back_populates="items")


class CodeSequence(Base):
    """Next item-code number, one counter per firm (`prefix` is always empty; the firm's
    optional code prefix is applied when a code is formatted). Row-locked on allocation;
    numbers are never reused, so a code stays unique even if its item is
    deleted. See `app/services/catalog/codes.py`.
    """

    __tablename__ = "code_sequence"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class SupplierCatalogDefault(PkUuidMixin, TimestampMixin, Base):
    """What a supplier usually gets, applied to each new catalog imported from them."""

    __tablename__ = "supplier_catalog_default"
    __table_args__ = (
        UniqueConstraint("tenant_id", "party_id", name="uq_supplier_catalog_default_party"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("party.id"), nullable=False)
    bulk_margin_pct: Mapped[Decimal | None] = mapped_column(_MARGIN)
    rounding_step: Mapped[int | None] = mapped_column(Integer)
    # our group name for what the rules suggest: {"beer mugs": "Drinkware"}
    group_map: Mapped[dict] = mapped_column(_JSON, default=dict, nullable=False)
    # promote every included row to an item right after an import
    add_automatically: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ... and mark those items In stock (otherwise they start Out of stock)
    mark_in_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SupplierPricePoint(PkUuidMixin, TimestampMixin, Base):
    """One observed price: what a supplier quoted in a price list, or what a bill charged."""

    __tablename__ = "supplier_price_point"
    __table_args__ = (
        Index("ix_price_point_product", "tenant_id", "product_id", "source", "on_date"),
        Index("ix_price_point_item", "tenant_id", "item_id", "source", "on_date"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    supplier_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_product.id"))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"))
    # quote (a price list) | bill (an approved inward bill)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    pack_qty: Mapped[int | None] = mapped_column(Integer)
    on_date: Mapped[date] = mapped_column(Date, nullable=False)


class CatalogOutputJob(PkUuidMixin, TimestampMixin, Base):
    """A generated file too big to build inside one request (label sheets of thousands).

    The request creates the row and a background task fills in `result_key`; the SPA
    polls the row. `params_json` holds what is needed to rebuild the output (item ids in
    print order, preset, options).
    """

    __tablename__ = "catalog_output_job"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    # The supplier catalog it belongs to (labels, today). Null for firm-level outputs built
    # from the item list (customer catalogs).
    catalog_id: Mapped[str | None] = mapped_column(
        ForeignKey("supplier_catalog.id"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # 'labels' | 'customer_catalog'
    params_json: Mapped[dict | None] = mapped_column(_JSON)
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    result_key: Mapped[str | None] = mapped_column(String(300))
    scan_warning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class CustomerCatalog(PkUuidMixin, TimestampMixin, Base):
    """A generated customer-facing catalog PDF, built from items you have (your prices and photos).

    Firm-level: it belongs to no supplier catalog. Each build is a snapshot; rebuilding the same
    `series_id` makes the next version from the stored selection (a filter is evaluated again, so
    a "Winter range" picks up new items). It is out of date when a member item changed after the
    build, or when a filter now matches different items; that is worked out when listing, not
    stored.
    """

    __tablename__ = "customer_catalog"
    __table_args__ = (
        UniqueConstraint("tenant_id", "series_id", "version", name="uq_customer_catalog_version"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    # Versions of one catalog share a series.
    series_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    options_json: Mapped[dict | None] = mapped_column(_JSON)
    # What was asked for: {"ids": [...]} or {"filter": {...}}. Rebuilds start from this.
    selection_json: Mapped[dict | None] = mapped_column(_JSON)
    # The items actually printed, in order: staleness is checked against them.
    member_ids_json: Mapped[list | None] = mapped_column(_JSON)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_key: Mapped[str] = mapped_column(String(300), nullable=False)


class CatalogProduct(PkUuidMixin, TimestampMixin, Base):
    """The shop's product, as seen across supplier catalogs.

    The item code belongs here, not to a PDF row: a later PDF that offers the same product
    (same supplier + same supplier code) reuses this row and its code, group and name. The
    code is a firm-wide running number that never changes (see `services/catalog/codes.py`).
    """

    __tablename__ = "catalog_product"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_catalog_product_tenant_code"),
        # One product per (supplier, supplier code). Without a supplier there is no key.
        Index(
            "uq_catalog_product_supplier_code",
            "tenant_id",
            "supplier_party_id",
            "supplier_code",
            unique=True,
            postgresql_where=text("supplier_party_id IS NOT NULL"),
            sqlite_where=text("supplier_party_id IS NOT NULL"),
        ),
        Index("ix_catalog_product_name", "tenant_id", "name_normalized"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    supplier_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"))
    supplier_code: Mapped[str] = mapped_column(String(60), nullable=False)

    code: Mapped[str] = mapped_column(String(20), nullable=False)

    category_id: Mapped[str | None] = mapped_column(ForeignKey("item_category.id"))
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(300), default="", nullable=False)

    # Set when the product is promoted to (or matched with) an item in the shop's item list.
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)

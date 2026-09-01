"""Item catalogue: items, aliases, and variant groups.

The catalogue accretes from what gets billed. `name_normalized` is the
dedupe key (lowercased, punctuation-stripped, synonym-mapped); a fuzzy
trigram index on it powers the type-ahead — the index is added in a
follow-up migration once pg_trgm is confirmed present.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import (
    AliasSource,
    ItemSource,
    ItemStatus,
    ItemType,
    PkUuidMixin,
    RateMode,
    TimestampMixin,
)


class ProductGroup(PkUuidMixin, TimestampMixin, Base):
    """A family of size/variant items (e.g. "SS Balti" in 5 sizes).

    The middle level of category → group → item. `name_normalized` +
    group aliases resolve wording drift ("ST Storage Box" / "ST Stor Box").
    `group_code` (dormant) is what a stack barcode encodes.
    """

    __tablename__ = "product_group"
    __table_args__ = (
        UniqueConstraint("tenant_id", "group_code", name="uq_group_tenant_code"),
        UniqueConstraint("tenant_id", "name_normalized", name="uq_group_tenant_normname"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=""
    )
    category: Mapped[str | None] = mapped_column(String(50))  # legacy; category_id wins
    category_id: Mapped[str | None] = mapped_column(ForeignKey("item_category.id"), index=True)
    hsn_code: Mapped[str | None] = mapped_column(ForeignKey("hsn_code.code"))
    uom: Mapped[str | None] = mapped_column(String(20))
    item_type: Mapped[ItemType] = mapped_column(String(10), default=ItemType.mrp, nullable=False)
    default_rate_mode: Mapped[RateMode] = mapped_column(
        String(10), default=RateMode.piece, nullable=False
    )
    group_code: Mapped[str | None] = mapped_column(String(32))
    default_size_pos: Mapped[int | None] = mapped_column(Integer)

    items: Mapped[list[Item]] = relationship(back_populates="group")


class Item(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "item"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name_normalized", name="uq_item_tenant_normname"),
        UniqueConstraint("group_id", "size_pos", name="uq_item_group_sizepos"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("product_group.id"), index=True)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(300), nullable=False)

    item_type: Mapped[ItemType] = mapped_column(String(10), default=ItemType.bulk, nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))  # legacy; category_id wins
    category_id: Mapped[str | None] = mapped_column(ForeignKey("item_category.id"), index=True)
    uom: Mapped[str | None] = mapped_column(String(20))
    hsn_code: Mapped[str | None] = mapped_column(ForeignKey("hsn_code.code"))

    # per piece | per kg (weight goods). Default from the group; the invoice
    # line copies it and may flip it for one bill.
    rate_mode: Mapped[RateMode] = mapped_column(
        String(10), default=RateMode.piece, nullable=False
    )
    # kg per one piece, when rate_mode = kg (used to split a pooled inward line
    # and, later, to convert pieces↔kg in the editor).
    weight_per_piece: Mapped[float | None] = mapped_column(Numeric(12, 3))

    # --- metal-trade attributes (all optional; sharpen search + the printed line) ---
    # metal: MS/SS/GI/aluminium/brass/copper/cast_iron
    metal: Mapped[str | None] = mapped_column(String(20))
    # shape: angle/channel/beam/flat/round_bar/sheet/coil/pipe/…
    shape: Mapped[str | None] = mapped_column(String(24))
    grade: Mapped[str | None] = mapped_column(String(32))  # "304", "IS2062", "6063"
    size_text: Mapped[str | None] = mapped_column(String(60))  # "40x40x5", "1250mm"
    thickness_mm: Mapped[float | None] = mapped_column(Numeric(9, 2))
    width_mm: Mapped[float | None] = mapped_column(Numeric(9, 2))
    length_mm: Mapped[float | None] = mapped_column(Numeric(9, 2))
    # finish: mill/polished/matte/galvanised/pvc_coated
    finish: Mapped[str | None] = mapped_column(String(24))

    # --- units & conversion (stored now; invoice-editor wiring is a later slice) ---
    secondary_uom: Mapped[str | None] = mapped_column(String(20))  # pcs / ft / m
    # 1 secondary unit = N primary units (e.g. 1 pipe = 6.2 kg)
    conversion_factor: Mapped[float | None] = mapped_column(Numeric(12, 4))
    weight_per_uom: Mapped[float | None] = mapped_column(Numeric(12, 3))  # theoretical kg
    purchase_uom: Mapped[str | None] = mapped_column(String(20))  # supplier's unit

    # Pricing
    default_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    last_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    last_sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    times_billed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mrp: Mapped[float | None] = mapped_column(Numeric(15, 2))
    default_discount_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))

    # Optimum price band — an out-of-range invoice rate warns, never blocks (M1).
    price_min: Mapped[float | None] = mapped_column(Numeric(15, 2))
    price_max: Mapped[float | None] = mapped_column(Numeric(15, 2))

    # Purchase side — bumped on inward-bill approve (ext_inward_import).
    last_purchase_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    last_purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- reserved for the price-suggestion engine (dormant this slice) ---
    markup_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    suggested_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    suggested_rate_basis: Mapped[str | None] = mapped_column(String(120))
    suggested_rate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_review_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- reserved for Stage 2/3 (dormant) ---
    barcode: Mapped[str | None] = mapped_column(String(64), index=True)
    sku: Mapped[str | None] = mapped_column(String(64))
    reorder_level: Mapped[float | None] = mapped_column(Numeric(15, 3))

    notes: Mapped[str | None] = mapped_column(String(1000))

    # Variant within a group
    size_pos: Mapped[int | None] = mapped_column(Integer)
    size_label: Mapped[str | None] = mapped_column(String(50))

    # Lifecycle
    source: Mapped[ItemSource] = mapped_column(
        String(20), default=ItemSource.manual, nullable=False
    )
    status: Mapped[ItemStatus] = mapped_column(
        String(20), default=ItemStatus.unconfirmed, nullable=False, index=True
    )
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"))
    tally_guid: Mapped[str | None] = mapped_column(String(64), index=True)

    # Stage 3+ — dormant.
    stock_tracking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stock_qty: Mapped[float | None] = mapped_column(Numeric(15, 3))
    gst_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)

    group: Mapped[ProductGroup | None] = relationship(back_populates="items")
    aliases: Mapped[list[ItemAlias]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ItemAlias(PkUuidMixin, TimestampMixin, Base):
    """A learned wording → catalogue-thing mapping.

    Points at exactly one of `item_id` (a leaf) or `group_id` (a family).
    `source` decides lifecycle: `auto_from_purchase` (off a real inward
    document) is permanent; `learned` (from the billing type-ahead) is
    swept by a nightly job if `last_used_at` falls behind 90 days.
    """

    __tablename__ = "item_alias"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alias_normalized", name="uq_alias_tenant_norm"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("product_group.id"), index=True)
    alias_text: Mapped[str] = mapped_column(String(300), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[AliasSource] = mapped_column(
        String(20), default=AliasSource.manual, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    item: Mapped[Item | None] = relationship(back_populates="aliases")

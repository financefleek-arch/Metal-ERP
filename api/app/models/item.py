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
    ItemSource,
    ItemStatus,
    ItemType,
    PkUuidMixin,
    TimestampMixin,
)


class ProductGroup(PkUuidMixin, TimestampMixin, Base):
    """A family of size/variant items (e.g. "SS Balti" in 5 sizes).

    Table exists from Milestone 1 but is unused until Stage 2 curation;
    `group_code` is what a stack barcode encodes.
    """

    __tablename__ = "product_group"
    __table_args__ = (
        UniqueConstraint("tenant_id", "group_code", name="uq_group_tenant_code"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))
    hsn_code: Mapped[str | None] = mapped_column(ForeignKey("hsn_code.code"))
    uom: Mapped[str | None] = mapped_column(String(20))
    item_type: Mapped[ItemType] = mapped_column(String(10), default=ItemType.mrp, nullable=False)
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
    category: Mapped[str | None] = mapped_column(String(50))
    uom: Mapped[str | None] = mapped_column(String(20))
    hsn_code: Mapped[str | None] = mapped_column(ForeignKey("hsn_code.code"))

    # Pricing
    default_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    last_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    last_sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    times_billed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mrp: Mapped[float | None] = mapped_column(Numeric(15, 2))
    default_discount_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))

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
    __tablename__ = "item_alias"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alias_normalized", name="uq_alias_tenant_norm"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("item.id"), nullable=False, index=True)
    alias_text: Mapped[str] = mapped_column(String(300), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(300), nullable=False)

    item: Mapped[Item] = relationship(back_populates="aliases")

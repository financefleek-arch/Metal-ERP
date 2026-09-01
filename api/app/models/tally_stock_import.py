"""Staging table for a Tally stock-items XML import.

One row per kept <STOCKITEM>. The review screen reads these; commit turns
the ready ones into `item` rows (seeding `hsn_code` for an unseen HSN) and
leaves the flagged ones here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import ItemType, PkUuidMixin, RateMode

try:  # pragma: no cover
    from sqlalchemy.dialects.postgresql import JSONB

    _JSON = JSON().with_variant(JSONB(), "postgresql")
except Exception:  # pragma: no cover
    _JSON = JSON()


class StagingTallyItem(PkUuidMixin, Base):
    __tablename__ = "staging_tally_item"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # --- from the XML ---
    tally_guid: Mapped[str | None] = mapped_column(String(64))
    stock_name: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_group: Mapped[str | None] = mapped_column(String(200))
    base_units: Mapped[str | None] = mapped_column(String(40))
    hsn: Mapped[str | None] = mapped_column(String(20))
    gst_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    standard_rate: Mapped[float | None] = mapped_column(Numeric(15, 2))
    raw_xml: Mapped[str | None] = mapped_column(Text)

    # --- parsed (product_parse) ---
    proposed_type: Mapped[ItemType] = mapped_column(
        String(10), default=ItemType.bulk, nullable=False
    )
    proposed_uom: Mapped[str | None] = mapped_column(String(20))
    proposed_rate_mode: Mapped[RateMode] = mapped_column(
        String(10), default=RateMode.piece, nullable=False
    )
    parsed_metal: Mapped[str | None] = mapped_column(String(20))
    parsed_shape: Mapped[str | None] = mapped_column(String(24))
    parsed_grade: Mapped[str | None] = mapped_column(String(32))
    parsed_size_text: Mapped[str | None] = mapped_column(String(60))
    parsed_sku: Mapped[str | None] = mapped_column(String(64))

    # --- matcher ---
    match_method: Mapped[str] = mapped_column(String(10), default="none", nullable=False)
    match_item_id: Mapped[str | None] = mapped_column(ForeignKey("item.id"))
    guid_fillable: Mapped[bool] = mapped_column(default=True, nullable=False)
    flags_json: Mapped[list[dict] | None] = mapped_column(_JSON)

    # --- reviewer ---
    # pending | create | link | skip
    decision: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    type_override: Mapped[str | None] = mapped_column(String(10))
    edited_name: Mapped[str | None] = mapped_column(String(200))
    seed_hsn: Mapped[bool] = mapped_column(default=False, nullable=False)

    committed_as: Mapped[str | None] = mapped_column(String(36))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

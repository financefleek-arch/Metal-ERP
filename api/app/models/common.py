"""Reference and infrastructure tables: HSN codes, name-normalization
synonyms, and the gap-free document number sequence.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class HsnCode(Base):
    """Shipped reference list of HSN/SAC codes. Seeded, rarely changed.

    `item.hsn_code` is an FK to this — HSN is a lookup, never free text.
    """

    __tablename__ = "hsn_code"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    chapter: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    default_gst_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    parent_code: Mapped[str | None] = mapped_column(String(8))
    valid_from: Mapped[date | None] = mapped_column()
    valid_to: Mapped[date | None] = mapped_column()


class Synonym(PkUuidMixin, TimestampMixin, Base):
    """Token rewrites applied during item-name normalization.

    e.g. from_token='stainless' -> to_token='ss'. Tenant-editable; seeded
    with ~30 metal-trade entries.
    """

    __tablename__ = "synonym"
    __table_args__ = (
        UniqueConstraint("tenant_id", "from_token", name="uq_synonym_tenant_from"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    from_token: Mapped[str] = mapped_column(String(100), nullable=False)
    to_token: Mapped[str] = mapped_column(String(100), nullable=False)


class NumberSequence(Base):
    """Per (tenant, series, financial-year) counter for gap-free document
    numbers. Claimed with SELECT ... FOR UPDATE inside the finalize
    transaction. FY runs Apr-Mar.
    """

    __tablename__ = "number_sequence"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), primary_key=True)
    series: Mapped[str] = mapped_column(String(20), primary_key=True)
    fy: Mapped[str] = mapped_column(String(9), primary_key=True)  # e.g. "2026-27"
    last_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

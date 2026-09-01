"""Per-tenant item category.

The top bucket a shop navigates by. For a utensil (bartan) shop these are
brands — Hawkins, Mintage, SINI, ST, GS. For a metal-bar shop they are
materials — Steel, Aluminium, Iron. Same table; the shop fills it however
it thinks. Seeded on register with a starter set the shop edits.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class ItemCategory(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "item_category"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_item_category_tenant_name"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

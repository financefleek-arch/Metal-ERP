"""Per-tenant learned classification rule.

Written when a user recategorises an `unconfirmed` item in the Items screen:
the distinctive phrase from the item's normalised name is stored, pointed at
the group they chose. The next import matches it on its own — import #2 is
smarter than import #1 with no code change. Mirrors the `item_alias`
learned-alias loop.

`source`:
  * "seed"    — reserved; the fixed table lives in code, not here
  * "learned" — taught by a recategorise action; swept if unused (like aliases)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class ItemClassifyRule(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "item_classify_rule"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "phrase_normalized", name="uq_classify_rule_tenant_phrase"
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id"), nullable=False, index=True
    )
    # the phrase to look for inside item.name_normalized (already normalised)
    phrase_normalized: Mapped[str] = mapped_column(String(120), nullable=False)
    department: Mapped[str] = mapped_column(String(60), nullable=False)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_group.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(20), default="learned", nullable=False)
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""Staging table for a Tally masters-XML party import.

One row per in-scope Tally ledger, parsed but not yet written to `party`.
The review screen reads these; commit turns the ready ones into
`party` + `party_address` rows and leaves the flagged ones here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models._mixins import PartyRole, PkUuidMixin

# JSONB on Postgres, plain JSON on SQLite (tests).
try:  # pragma: no cover - trivial import guard
    from sqlalchemy.dialects.postgresql import JSONB

    _JSON = JSON().with_variant(JSONB(), "postgresql")
except Exception:  # pragma: no cover
    _JSON = JSON()


class StagingTallyParty(PkUuidMixin, Base):
    """A parsed Tally ledger awaiting review + import."""

    __tablename__ = "staging_tally_party"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # --- straight from the XML ---
    tally_guid: Mapped[str | None] = mapped_column(String(64))
    ledger_name: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_group: Mapped[str | None] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(20))
    pan: Mapped[str | None] = mapped_column(String(20))
    state_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(200))
    address_lines_json: Mapped[list[str] | None] = mapped_column(_JSON)
    pincode: Mapped[str | None] = mapped_column(String(20))
    raw_xml: Mapped[str | None] = mapped_column(Text)

    # --- computed by the matcher ---
    proposed_role: Mapped[PartyRole] = mapped_column(
        String(10), default=PartyRole.customer, nullable=False
    )
    # exact_gstin | exact_pan | name_fuzzy | none
    match_method: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    match_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"))
    # list of {code,message}
    flags_json: Mapped[list[dict] | None] = mapped_column(_JSON)

    # --- the reviewer's decision ---
    # pending | create | link | skip
    decision: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    role_override: Mapped[str | None] = mapped_column(String(10))
    link_party_id: Mapped[str | None] = mapped_column(ForeignKey("party.id"))
    edited_name: Mapped[str | None] = mapped_column(String(200))

    # committed | None
    committed_as: Mapped[str | None] = mapped_column(String(36))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

"""Tally Connector (F1) — company link, per-record cross-reference, sync jobs.

F1a scope: masters-**in** only. A `tally_company` links a Metal-ERP tenant
to one Tally company served by one companion-agent install (`backup_shop`).
A `tally_sync_job` of kind `pull_masters` enqueues an `AgentOutboxItem`; the
agent reads the newest masters `*.xml` from its export folder, uploads it to
R2, and calls back `/api/tally-agent/jobs/{id}/result`. The backend parses
that XML into a staging batch (reusing `staging_tally_party` /
`staging_tally_item`) and records a `tally_link` per matched/created record.

Relationship to `inward.TallyLedgerConfig`: that is the inward-bill → Tally
purchase-voucher *export* config (F5 territory), PK'd on tenant_id, one flat
row of GST-ledger names. `tally_company.ledger_map` overlaps it but also
carries `shop_id`, `company_name`, `known_ledgers` and sync state. The two
are deliberately not merged in F1a — converging them is an F1b/F1c task once
voucher-out actually needs the ledger names.

`party.tally_guid` / `item.tally_guid` predate this module (written by the
manual Tally importer). `tally_link` is the forward-looking generic index —
F1a keys upserts on the existing columns first, then backfills `tally_link`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON as _JSONType

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin

# JSONB on Postgres, plain JSON on SQLite (tests) — same pattern as
# `models/tally_import.py`.
try:  # pragma: no cover - trivial import guard
    from sqlalchemy.dialects.postgresql import JSONB

    _JSON = _JSONType().with_variant(JSONB(), "postgresql")
except Exception:  # pragma: no cover
    _JSON = _JSONType()


class TallyCompany(PkUuidMixin, TimestampMixin, Base):
    """One linked Tally company per tenant (M1). `shop_id` is the
    companion-agent install that serves this company's export folder.
    """

    __tablename__ = "tally_company"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tally_company_tenant"),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    # 'file' (F1a — agent reads an export folder) | 'gateway' (F1c — agent
    # POSTs http://localhost:9000). Always 'file' in F1a.
    transport: Mapped[str] = mapped_column(String(8), default="file", nullable=False)

    # Which companion-agent install serves this company. Required before a
    # pull can be enqueued (router 422s if null).
    shop_id: Mapped[str | None] = mapped_column(
        ForeignKey("backup_shop.id"), index=True
    )

    # Operator-picked Tally ledger / group NAMES:
    #   {sales_ledger, cash_ledger, bank_ledger, round_off_ledger,
    #    cgst_ledger, sgst_ledger, igst_ledger,
    #    debtors_parent, creditors_parent}
    # F1a only reads debtors_parent / creditors_parent (party scoping); the
    # rest is collected in the UI for F1b (voucher-out) and unused here.
    ledger_map: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)

    # Last enumerated [{"name": ..., "parent": ...}] from a masters pull —
    # feeds the ledger-map dropdowns. Derived from the pull XML, not a
    # separate request.
    known_ledgers: Mapped[list | None] = mapped_column(_JSON)

    last_masters_pull_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class TallyLink(PkUuidMixin, Base):
    """Cross-reference: one Metal-ERP record <-> its Tally master.

    `checksum` is a hash of the fields last pulled from Tally, so a re-pull
    with identical data is a no-op (counted `skipped`). `last_pushed_at` is
    F1b.
    """

    __tablename__ = "tally_link"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entity_type", "entity_id",
            name="uq_tally_link_entity",
        ),
        UniqueConstraint(
            "tenant_id", "entity_type", "tally_guid",
            name="uq_tally_link_guid",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id"), nullable=False, index=True
    )
    # 'party' | 'item' | 'group'  (F1b adds 'invoice' | 'payment')
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    tally_guid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tally_masterid: Mapped[str | None] = mapped_column(String(32))

    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TallySyncJob(PkUuidMixin, Base):
    """One sync operation. F1a: `direction='in'`, `kind='pull_masters'`.

    Lifecycle: queued -> sent (agent picked up the outbox item) -> running
    (result callback received, parsing) -> ok | error.
    """

    __tablename__ = "tally_sync_job"
    __table_args__ = (
        # "is a pull already in flight for this tenant?" hits this a lot.
        Index("ix_tally_sync_job_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("tally_company.id"), nullable=False, index=True
    )

    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # 'in' | 'out'
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # 'pull_masters'
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False)

    outbox_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_outbox_item.id")
    )
    r2_key: Mapped[str | None] = mapped_column(String(500))
    batch_id: Mapped[str | None] = mapped_column(String(36), index=True)

    # Last "not ready" reason the agent pinged while the job was `sent` —
    # 'no_company_loaded' | 'tally_unavailable'. Drives the Pull step
    # indicator's "Waiting on Tally" sub-label and the lazy auto-cancel of a
    # pull that never gets a real result.
    last_agent_status: Mapped[str | None] = mapped_column(String(32))
    last_agent_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # {ledgers, items, groups, matched, created, filled, opening_filled, skipped}
    counts: Mapped[dict | None] = mapped_column(_JSON)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

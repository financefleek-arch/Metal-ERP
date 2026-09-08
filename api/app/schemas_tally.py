"""Pydantic I/O for the Tally Connector API (`/api/tally/*`).

F1a scope: register a Tally company, save its ledger map, trigger + track a
masters-in pull.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# The ledger-name slots the operator maps. Only debtors_parent /
# creditors_parent are read in F1a (party scoping); the GST-ledger names are
# collected for F1b voucher-out.
_LEDGER_MAP_KEYS = (
    "sales_ledger",
    "cash_ledger",
    "bank_ledger",
    "round_off_ledger",
    "cgst_ledger",
    "sgst_ledger",
    "igst_ledger",
    "debtors_parent",
    "creditors_parent",
)


class TallyCompanyIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    # `shop_id` is resolved server-side from the firm's provisioned agent
    # (one per firm) — not accepted here.


class KnownLedger(BaseModel):
    name: str
    parent: str | None = None
    kind: str  # 'ledger' | 'group'


class TallyCompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    company_name: str
    base_currency: str
    transport: str
    shop_id: str | None
    ledger_map: dict[str, str]
    known_ledgers: list[KnownLedger] | None
    last_masters_pull_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LedgerMapIn(BaseModel):
    """Partial — only the keys present are written; an empty string clears a
    slot. Unknown keys are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    sales_ledger: str | None = None
    cash_ledger: str | None = None
    bank_ledger: str | None = None
    round_off_ledger: str | None = None
    cgst_ledger: str | None = None
    sgst_ledger: str | None = None
    igst_ledger: str | None = None
    debtors_parent: str | None = None
    creditors_parent: str | None = None


class TallySyncJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    direction: str
    kind: str
    status: str
    r2_key: str | None
    batch_id: str | None
    counts: dict | None
    error: str | None
    last_agent_status: str | None
    last_agent_status_at: datetime | None
    created_at: datetime
    completed_at: datetime | None


class JobResultIn(BaseModel):
    """Agent -> backend callback for a `tally_sync_job`."""

    status: str = Field(pattern="^(ok|error)$")
    r2_key: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=2000)
    # optional: the newest export file's mtime, so the UI can warn "stale"
    file_mtime: datetime | None = None


class JobStatusPingIn(BaseModel):
    """Agent -> backend: a 'not ready' reason instead of a real result."""

    agent_status: str = Field(pattern="^(no_company_loaded|tally_unavailable)$")

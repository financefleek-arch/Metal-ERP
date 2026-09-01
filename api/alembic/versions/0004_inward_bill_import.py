"""inward bill import: tables + tenant flag + job queue

Adds the Inward Bill Import module (docs/EXTENSION-inward-bill-import.md):
  - inward_bill, inward_bill_line, supplier_template, tally_ledger_config,
    extraction_run  — all tenant-scoped
  - job  — a minimal Postgres-row work queue (X7 batch extraction)
  - tenant.ext_inward_import BOOLEAN DEFAULT false  — the feature flag
  - item.last_purchase_rate / item.last_purchased_at  — bumped on approve

No extension dependency (pg_trgm was created in 0002; the supplier name-fuzzy
match reuses the ix_party_legal_name_trgm index from 0003).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

_JSON = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(15, 2)
_QTY = sa.Numeric(15, 3)
_RATE = sa.Numeric(5, 2)
_CONF = sa.Numeric(4, 3)
_TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "ext_inward_import", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column("item", sa.Column("last_purchase_rate", _MONEY))
    op.add_column("item", sa.Column("last_purchased_at", _TS))

    op.create_table(
        "inward_bill",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("source_pdf_path", sa.String(500)),
        sa.Column("supplier_name", sa.String(200)),
        sa.Column("supplier_gstin", sa.String(15)),
        sa.Column("supplier_pan", sa.String(10)),
        sa.Column("supplier_state_code", sa.String(2)),
        sa.Column("matched_party_id", sa.String(36), sa.ForeignKey("party.id")),
        sa.Column("new_supplier_staged_json", _JSON),
        sa.Column("bill_no", sa.String(50)),
        sa.Column("bill_date", sa.Date()),
        sa.Column("sales_order_ref", sa.String(50)),
        sa.Column("place_of_supply_state_code", sa.String(2)),
        sa.Column("supply_type", sa.String(10)),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("taxable_total", _MONEY),
        sa.Column("cgst_total", _MONEY),
        sa.Column("sgst_total", _MONEY),
        sa.Column("igst_total", _MONEY),
        sa.Column("cess_total", _MONEY),
        sa.Column("round_off", _MONEY),
        sa.Column("grand_total", _MONEY),
        sa.Column("amount_in_words", sa.String(500)),
        sa.Column("extraction_method", sa.String(20)),
        sa.Column("extraction_confidence", _CONF),
        sa.Column("reconciled", sa.Boolean()),
        sa.Column("reconcile_discrepancy", _MONEY),
        sa.Column("status", sa.String(20), server_default="uploaded", nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("tally_xml_path", sa.String(500)),
        sa.Column("raw_text", sa.Text()),
        sa.Column("notes", sa.Text()),
        *_timestamps(),
    )
    op.create_index("ix_inward_bill_tenant_id", "inward_bill", ["tenant_id"])
    op.create_index("ix_inward_bill_matched_party_id", "inward_bill", ["matched_party_id"])
    op.create_index("ix_inward_bill_status", "inward_bill", ["status"])

    op.create_table(
        "inward_bill_line",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "inward_bill_id", sa.String(36), sa.ForeignKey("inward_bill.id"), nullable=False
        ),
        sa.Column("sl_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("hsn", sa.String(8)),
        sa.Column("quantity", _QTY),
        sa.Column("uom", sa.String(20)),
        sa.Column("unit_rate", _MONEY),
        sa.Column("discount_pct", _RATE),
        sa.Column("discount_amt", _MONEY),
        sa.Column("taxable_value", _MONEY),
        sa.Column("cgst_rate", _RATE),
        sa.Column("cgst_amt", _MONEY),
        sa.Column("sgst_rate", _RATE),
        sa.Column("sgst_amt", _MONEY),
        sa.Column("igst_rate", _RATE),
        sa.Column("igst_amt", _MONEY),
        sa.Column("line_total", _MONEY),
        sa.Column("match_method", sa.String(10)),
        sa.Column("match_confidence", _CONF),
        sa.Column("matched_item_id", sa.String(36), sa.ForeignKey("item.id")),
        sa.Column("new_item_staged_json", _JSON),
        sa.Column("review_flag", sa.String(20)),
        *_timestamps(),
    )
    op.create_index(
        "ix_inward_bill_line_inward_bill_id", "inward_bill_line", ["inward_bill_id"]
    )
    op.create_index(
        "ix_inward_bill_line_matched_item_id", "inward_bill_line", ["matched_item_id"]
    )

    op.create_table(
        "supplier_template",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("supplier_gstin", sa.String(15), nullable=False),
        sa.Column("supplier_name", sa.String(200)),
        sa.Column("column_ranges_json", _JSON),
        sa.Column("header_anchors_json", _JSON),
        sa.Column("uom_map_json", _JSON),
        sa.Column("default_purchase_ledger", sa.String(100)),
        sa.Column("default_cgst_ledger", sa.String(100)),
        sa.Column("default_sgst_ledger", sa.String(100)),
        sa.Column("default_igst_ledger", sa.String(100)),
        sa.Column("created_from_bill_id", sa.String(36), sa.ForeignKey("inward_bill.id")),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "supplier_gstin", name="uq_supplier_template_tenant_gstin"
        ),
    )
    op.create_index("ix_supplier_template_tenant_id", "supplier_template", ["tenant_id"])

    op.create_table(
        "tally_ledger_config",
        sa.Column(
            "tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), primary_key=True
        ),
        sa.Column(
            "creditors_group",
            sa.String(100),
            server_default="Sundry Creditors",
            nullable=False,
        ),
        sa.Column(
            "purchase_ledger",
            sa.String(100),
            server_default="Purchase Accounts",
            nullable=False,
        ),
        sa.Column("cgst_ledger", sa.String(100), server_default="CGST", nullable=False),
        sa.Column("sgst_ledger", sa.String(100), server_default="SGST", nullable=False),
        sa.Column("igst_ledger", sa.String(100), server_default="IGST", nullable=False),
        sa.Column(
            "round_off_ledger", sa.String(100), server_default="Round Off", nullable=False
        ),
        sa.Column("xml_encoding", sa.String(10), server_default="UTF-16", nullable=False),
    )

    op.create_table(
        "extraction_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "inward_bill_id", sa.String(36), sa.ForeignKey("inward_bill.id"), nullable=False
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(20)),
        sa.Column("ok", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("confidence", _CONF),
        sa.Column("error", sa.Text()),
        sa.Column("llm_tokens", sa.Integer()),
        *_timestamps(),
    )
    op.create_index(
        "ix_extraction_run_inward_bill_id", "extraction_run", ["inward_bill_id"]
    )

    op.create_table(
        "job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload_json", _JSON),
        sa.Column("status", sa.String(10), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", _TS),
        sa.Column("finished_at", _TS),
    )
    op.create_index("ix_job_tenant_id", "job", ["tenant_id"])
    op.create_index("ix_job_status", "job", ["status"])


def downgrade() -> None:
    for tbl in (
        "job",
        "extraction_run",
        "tally_ledger_config",
        "supplier_template",
        "inward_bill_line",
        "inward_bill",
    ):
        op.drop_table(tbl)

    op.drop_column("item", "last_purchased_at")
    op.drop_column("item", "last_purchase_rate")
    op.drop_column("tenant", "ext_inward_import")

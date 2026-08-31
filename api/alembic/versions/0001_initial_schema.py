"""initial schema

Full Metal ERP schema. Columns for later maturity stages (GST, stock,
barcode, weighbridge) are created now, nullable/defaulted and unused.
The pg_trgm fuzzy index on item.name_normalized is added in 0002 so this
migration has no extension dependency.

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alembic import op

# JSONB on Postgres, plain JSON elsewhere (SQLite in tests/CI).
_JSON = JSON().with_variant(JSONB(), "postgresql")

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(15, 2)
_QTY = sa.Numeric(15, 3)
_RATE = sa.Numeric(5, 2)
_TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "hsn_code",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("chapter", sa.String(2), nullable=False),
        sa.Column("default_gst_rate", _RATE),
        sa.Column("parent_code", sa.String(8)),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
    )
    op.create_index("ix_hsn_code_chapter", "hsn_code", ["chapter"])

    op.create_table(
        "tenant",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("trade_name", sa.String(200)),
        sa.Column("pan", sa.String(10)),
        sa.Column("address", sa.Text()),
        sa.Column("city", sa.String(100)),
        sa.Column("state_code", sa.String(2)),
        sa.Column("pincode", sa.String(6)),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(200)),
        sa.Column("logo_url", sa.String(500)),
        sa.Column("bank_holder", sa.String(200)),
        sa.Column("bank_name", sa.String(200)),
        sa.Column("bank_ac_no", sa.String(50)),
        sa.Column("bank_ifsc", sa.String(20)),
        sa.Column("bank_branch", sa.String(200)),
        sa.Column("upi_id", sa.String(100)),
        sa.Column("declaration_text", sa.Text()),
        sa.Column("terms_text", sa.Text()),
        sa.Column("jurisdiction_text", sa.Text()),
        sa.Column("document_label", sa.String(50), server_default="Invoice", nullable=False),
        sa.Column("gst_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("gstin", sa.String(15)),
        *_timestamps(),
    )

    op.create_table(
        "app_user",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), server_default="owner", nullable=False),
        sa.Column("totp_secret", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
    )
    op.create_index("ix_app_user_tenant_id", "app_user", ["tenant_id"])

    op.create_table(
        "number_sequence",
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), primary_key=True),
        sa.Column("series", sa.String(20), primary_key=True),
        sa.Column("fy", sa.String(9), primary_key=True),
        sa.Column("last_value", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "synonym",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("from_token", sa.String(100), nullable=False),
        sa.Column("to_token", sa.String(100), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "from_token", name="uq_synonym_tenant_from"),
    )
    op.create_index("ix_synonym_tenant_id", "synonym", ["tenant_id"])

    op.create_table(
        "party",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(200)),
        sa.Column("pan", sa.String(10)),
        sa.Column("role", sa.String(10), server_default="customer", nullable=False),
        sa.Column("default_state_code", sa.String(2)),
        sa.Column("gstin", sa.String(15)),
        sa.Column("tally_guid", sa.String(64)),
        *_timestamps(),
    )
    op.create_index("ix_party_tenant_id", "party", ["tenant_id"])
    op.create_index("ix_party_tally_guid", "party", ["tally_guid"])

    op.create_table(
        "party_address",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=False),
        sa.Column("type", sa.String(10), server_default="both", nullable=False),
        sa.Column("line1", sa.String(200)),
        sa.Column("line2", sa.String(200)),
        sa.Column("line3", sa.String(200)),
        sa.Column("city", sa.String(100)),
        sa.Column("state_code", sa.String(2)),
        sa.Column("pincode", sa.String(6)),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_party_address_party_id", "party_address", ["party_id"])

    op.create_table(
        "product_group",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(50)),
        sa.Column("hsn_code", sa.String(8), sa.ForeignKey("hsn_code.code")),
        sa.Column("uom", sa.String(20)),
        sa.Column("item_type", sa.String(10), server_default="mrp", nullable=False),
        sa.Column("group_code", sa.String(32)),
        sa.Column("default_size_pos", sa.Integer()),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "group_code", name="uq_group_tenant_code"),
    )
    op.create_index("ix_product_group_tenant_id", "product_group", ["tenant_id"])

    op.create_table(
        "item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("product_group.id")),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("name_normalized", sa.String(300), nullable=False),
        sa.Column("item_type", sa.String(10), server_default="bulk", nullable=False),
        sa.Column("category", sa.String(50)),
        sa.Column("uom", sa.String(20)),
        sa.Column("hsn_code", sa.String(8), sa.ForeignKey("hsn_code.code")),
        sa.Column("default_rate", _MONEY),
        sa.Column("last_rate", _MONEY),
        sa.Column("last_sold_at", _TS),
        sa.Column("times_billed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mrp", _MONEY),
        sa.Column("default_discount_pct", _RATE),
        sa.Column("size_pos", sa.Integer()),
        sa.Column("size_label", sa.String(50)),
        sa.Column("source", sa.String(20), server_default="manual", nullable=False),
        sa.Column("status", sa.String(20), server_default="unconfirmed", nullable=False),
        sa.Column("merged_into_id", sa.String(36), sa.ForeignKey("item.id")),
        sa.Column("tally_guid", sa.String(64)),
        sa.Column("stock_tracking", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("stock_qty", _QTY),
        sa.Column("gst_rate", _RATE, server_default="0", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "name_normalized", name="uq_item_tenant_normname"),
        sa.UniqueConstraint("group_id", "size_pos", name="uq_item_group_sizepos"),
    )
    op.create_index("ix_item_tenant_id", "item", ["tenant_id"])
    op.create_index("ix_item_group_id", "item", ["group_id"])
    op.create_index("ix_item_status", "item", ["status"])
    op.create_index("ix_item_tally_guid", "item", ["tally_guid"])

    op.create_table(
        "item_alias",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id"), nullable=False),
        sa.Column("alias_text", sa.String(300), nullable=False),
        sa.Column("alias_normalized", sa.String(300), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "alias_normalized", name="uq_alias_tenant_norm"),
    )
    op.create_index("ix_item_alias_tenant_id", "item_alias", ["tenant_id"])
    op.create_index("ix_item_alias_item_id", "item_alias", ["item_id"])

    op.create_table(
        "invoice",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("doc_type", sa.String(4), server_default="inv", nullable=False),
        sa.Column("series", sa.String(20), server_default="Sales", nullable=False),
        sa.Column("number", sa.Integer()),
        sa.Column("fy", sa.String(9), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("party_id", sa.String(36), sa.ForeignKey("party.id"), nullable=False),
        sa.Column("bill_to_addr_id", sa.String(36), sa.ForeignKey("party_address.id")),
        sa.Column("ship_to_addr_id", sa.String(36), sa.ForeignKey("party_address.id")),
        sa.Column("notes", sa.Text()),
        sa.Column("terms_snapshot", sa.Text()),
        sa.Column("declaration_snapshot", sa.Text()),
        sa.Column("status", sa.String(10), server_default="draft", nullable=False),
        sa.Column("template_version", sa.String(20), server_default="v1-nongst", nullable=False),
        sa.Column("pdf_path", sa.String(500)),
        sa.Column("pdf_status", sa.String(10), server_default="none", nullable=False),
        sa.Column("subtotal", _MONEY),
        sa.Column("discount_total", _MONEY),
        sa.Column("round_off", _MONEY),
        sa.Column("grand_total", _MONEY),
        sa.Column("amount_in_words", sa.String(500)),
        sa.Column("place_of_supply_state_code", sa.String(2)),
        sa.Column("supply_type", sa.String(20)),
        sa.Column("reverse_charge", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("taxable_total", _MONEY),
        sa.Column("cgst_total", _MONEY),
        sa.Column("sgst_total", _MONEY),
        sa.Column("igst_total", _MONEY),
        sa.Column("cess_total", _MONEY),
        sa.Column("tax_in_words", sa.String(500)),
        sa.Column("irn", sa.String(100)),
        sa.Column("ack_no", sa.String(50)),
        sa.Column("ack_date", _TS),
        sa.Column("signed_qr", sa.Text()),
        sa.Column("signed_invoice", sa.Text()),
        sa.Column("ewb_no", sa.String(20)),
        sa.Column("ewb_date", _TS),
        sa.Column("ewb_valid_till", _TS),
        sa.Column("distance_km", sa.Integer()),
        sa.Column("transport_mode", sa.String(20)),
        sa.Column("vehicle_no", sa.String(20)),
        sa.Column("transporter_id", sa.String(20)),
        sa.Column("gstn_status", sa.String(20)),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "series", "fy", "number", name="uq_invoice_tenant_series_fy_number"
        ),
    )
    op.create_index("ix_invoice_tenant_id", "invoice", ["tenant_id"])
    op.create_index("ix_invoice_party_id", "invoice", ["party_id"])
    op.create_index("ix_invoice_status", "invoice", ["status"])

    op.create_table(
        "invoice_line",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoice.id"), nullable=False),
        sa.Column("sl_no", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("item.id")),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("hsn_code", sa.String(8), sa.ForeignKey("hsn_code.code")),
        sa.Column("quantity", _QTY, nullable=False),
        sa.Column("uom", sa.String(20)),
        sa.Column("unit_rate", _MONEY, nullable=False),
        sa.Column("discount", _MONEY, server_default="0", nullable=False),
        sa.Column("line_total", _MONEY),
        sa.Column("gst_rate", _RATE, server_default="0", nullable=False),
        sa.Column("is_rate_inclusive", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("taxable_value", _MONEY),
        sa.Column("cgst_amt", _MONEY),
        sa.Column("sgst_amt", _MONEY),
        sa.Column("igst_amt", _MONEY),
        sa.Column("cess_amt", _MONEY),
        sa.Column("weighment_id", sa.String(36)),
        sa.Column("stock_lot_id", sa.String(36)),
        sa.Column("size_pos", sa.Integer()),
        *_timestamps(),
    )
    op.create_index("ix_invoice_line_invoice_id", "invoice_line", ["invoice_id"])
    op.create_index("ix_invoice_line_item_id", "invoice_line", ["item_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("entity", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("before_json", _JSON),
        sa.Column("after_json", _JSON),
        *_timestamps(),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity"])
    op.create_index("ix_audit_log_entity_id", "audit_log", ["entity_id"])


def downgrade() -> None:
    for tbl in (
        "audit_log",
        "invoice_line",
        "invoice",
        "item_alias",
        "item",
        "product_group",
        "party_address",
        "party",
        "synonym",
        "number_sequence",
        "app_user",
        "tenant",
        "hsn_code",
    ):
        op.drop_table(tbl)

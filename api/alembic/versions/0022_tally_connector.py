"""Tally Connector (F1a) — company link, cross-reference, sync jobs.

Three new tables:

* `tally_company` — one row per tenant: the linked Tally company, the
  companion-agent install (`backup_shop`) that serves its export folder, and
  the operator's ledger-name map (only debtors/creditors parents are read in
  F1a; the GST-ledger names are collected for F1b voucher-out).
* `tally_link` — cross-reference between a Metal-ERP record and its Tally
  master (guid + checksum), so a re-pull with identical data is a no-op.
* `tally_sync_job` — one masters-pull operation, its status, the R2 key of
  the uploaded XML, the staging batch it produced, and result counts.

Also adds `sync_job_id` to `staging_tally_party` / `staging_tally_item` so a
staged row traces back to the pull that produced it (nullable — a plain
file-upload batch leaves it null).

Nothing here is enforced DB-level beyond FKs + the two `tally_link`
uniqueness rules; the "one pull in flight per tenant" guard is a router
concern (same pattern as the payment invariants in 0017).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, plain JSON on SQLite (tests).
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tally_company",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column(
            "base_currency", sa.String(length=3), nullable=False, server_default="INR"
        ),
        sa.Column(
            "transport", sa.String(length=8), nullable=False, server_default="file"
        ),
        sa.Column("shop_id", sa.String(length=36), nullable=True),
        sa.Column("ledger_map", _JSON, nullable=False, server_default="{}"),
        sa.Column("known_ledgers", _JSON, nullable=True),
        sa.Column(
            "last_masters_pull_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.ForeignKeyConstraint(["shop_id"], ["backup_shop.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tally_company_tenant"),
    )
    op.create_index(
        "ix_tally_company_tenant_id", "tally_company", ["tenant_id"]
    )
    op.create_index("ix_tally_company_shop_id", "tally_company", ["shop_id"])

    op.create_table(
        "tally_link",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=10), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("tally_guid", sa.String(length=64), nullable=False),
        sa.Column("tally_masterid", sa.String(length=32), nullable=True),
        sa.Column("last_pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "entity_type", "entity_id", name="uq_tally_link_entity"
        ),
        sa.UniqueConstraint(
            "tenant_id", "entity_type", "tally_guid", name="uq_tally_link_guid"
        ),
    )
    op.create_index("ix_tally_link_tenant_id", "tally_link", ["tenant_id"])
    op.create_index("ix_tally_link_entity_id", "tally_link", ["entity_id"])
    op.create_index("ix_tally_link_tally_guid", "tally_link", ["tally_guid"])

    op.create_table(
        "tally_sync_job",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="queued"
        ),
        sa.Column("outbox_item_id", sa.String(length=36), nullable=True),
        sa.Column("r2_key", sa.String(length=500), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("last_agent_status", sa.String(length=32), nullable=True),
        sa.Column("last_agent_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts", _JSON, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["tally_company.id"]),
        sa.ForeignKeyConstraint(["outbox_item_id"], ["agent_outbox_item.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tally_sync_job_tenant_id", "tally_sync_job", ["tenant_id"]
    )
    op.create_index(
        "ix_tally_sync_job_company_id", "tally_sync_job", ["company_id"]
    )
    op.create_index("ix_tally_sync_job_batch_id", "tally_sync_job", ["batch_id"])
    op.create_index(
        "ix_tally_sync_job_tenant_status",
        "tally_sync_job",
        ["tenant_id", "status"],
    )

    for table in ("staging_tally_party", "staging_tally_item"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("sync_job_id", sa.String(length=36), nullable=True)
            )


def downgrade() -> None:
    for table in ("staging_tally_party", "staging_tally_item"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("sync_job_id")

    op.drop_index("ix_tally_sync_job_tenant_status", table_name="tally_sync_job")
    op.drop_index("ix_tally_sync_job_batch_id", table_name="tally_sync_job")
    op.drop_index("ix_tally_sync_job_company_id", table_name="tally_sync_job")
    op.drop_index("ix_tally_sync_job_tenant_id", table_name="tally_sync_job")
    op.drop_table("tally_sync_job")

    op.drop_index("ix_tally_link_tally_guid", table_name="tally_link")
    op.drop_index("ix_tally_link_entity_id", table_name="tally_link")
    op.drop_index("ix_tally_link_tenant_id", table_name="tally_link")
    op.drop_table("tally_link")

    op.drop_index("ix_tally_company_shop_id", table_name="tally_company")
    op.drop_index("ix_tally_company_tenant_id", table_name="tally_company")
    op.drop_table("tally_company")

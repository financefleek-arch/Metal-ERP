"""Consent to be sent price lists / catalogs on WhatsApp, kept on the party.

  - party.wa_catalog_consent         none | opted_in | opted_out (existing parties: none)
  - party.wa_catalog_consent_at      when it last changed
  - party.wa_catalog_consent_source  manual | order_page | whatsapp_reply
  - party.wa_catalog_consent_by      the user who recorded it (manual only)

No backfill: nobody is opted in until the shop records it. The old `whatsapp_optin` flag is left
alone and unread.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("party") as batch:
        batch.add_column(
            sa.Column("wa_catalog_consent", sa.String(10), nullable=False, server_default="none")
        )
        batch.add_column(sa.Column("wa_catalog_consent_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("wa_catalog_consent_source", sa.String(16)))
        batch.add_column(
            sa.Column("wa_catalog_consent_by", sa.String(36), sa.ForeignKey("app_user.id"))
        )


def downgrade() -> None:
    with op.batch_alter_table("party") as batch:
        batch.drop_column("wa_catalog_consent_by")
        batch.drop_column("wa_catalog_consent_source")
        batch.drop_column("wa_catalog_consent_at")
        batch.drop_column("wa_catalog_consent")

"""Customer ordering O4: WhatsApp notifications about orders.

  - whatsapp_message.order_id / order_event   which order and which moment a message was about
  - tenant.order_alert_phone                  the shop's own number for new-order alerts

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_message",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("customer_order.id"), nullable=True),
    )
    op.add_column("whatsapp_message", sa.Column("order_event", sa.String(20), nullable=True))
    op.create_index("ix_whatsapp_message_order_id", "whatsapp_message", ["order_id"])
    op.add_column("tenant", sa.Column("order_alert_phone", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant", "order_alert_phone")
    op.drop_index("ix_whatsapp_message_order_id", table_name="whatsapp_message")
    op.drop_column("whatsapp_message", "order_event")
    op.drop_column("whatsapp_message", "order_id")

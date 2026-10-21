"""The customer's "offers on WhatsApp" tick on an order.

  - customer_order.offers_opt_in   ticked the separate offers box on the order page. It waits on the
                                   order until it is tied to a customer, then becomes their consent.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_order",
        sa.Column("offers_opt_in", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("customer_order", "offers_opt_in")

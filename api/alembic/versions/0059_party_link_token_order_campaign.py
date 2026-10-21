"""A customer's personal link token, and which campaign an order came from.

  - party.link_token             the customer's own catalog link token (unique; minted when pushed to the CRM)
  - customer_order.campaign_ref  the campaign reference carried on a personal link (?c=...)

See docs/PLAN-fleek-control-plane.md (P5b).

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("party", sa.Column("link_token", sa.String(length=40), nullable=True))
    op.create_unique_constraint("uq_party_link_token", "party", ["link_token"])
    op.add_column("customer_order", sa.Column("campaign_ref", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("customer_order", "campaign_ref")
    op.drop_constraint("uq_party_link_token", "party", type_="unique")
    op.drop_column("party", "link_token")

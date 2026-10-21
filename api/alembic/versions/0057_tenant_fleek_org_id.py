"""Link a firm to its Fleek control-plane organisation.

  - tenant.fleek_org_id   the Fleek organisation id (unique, NULL until linked)

See docs/PLAN-fleek-control-plane.md (P1).

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("fleek_org_id", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_tenant_fleek_org_id", "tenant", ["fleek_org_id"])


def downgrade() -> None:
    op.drop_constraint("uq_tenant_fleek_org_id", "tenant", type_="unique")
    op.drop_column("tenant", "fleek_org_id")

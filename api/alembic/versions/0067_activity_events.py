"""Shop activity: what people do after sign-in, plus when they were last seen and whether they saw the welcome.

Additive only: a new table and two nullable columns. Nothing existing changes.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("props", sa.JSON().with_variant(JSONB(), "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_activity_event_tenant_id", "activity_event", ["tenant_id"])
    op.create_index("ix_activity_tenant_key_at", "activity_event", ["tenant_id", "key", "created_at"])
    with op.batch_alter_table("app_user") as b:
        b.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("welcome_seen_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("app_user") as b:
        b.drop_column("welcome_seen_at")
        b.drop_column("last_seen_at")
    op.drop_table("activity_event")

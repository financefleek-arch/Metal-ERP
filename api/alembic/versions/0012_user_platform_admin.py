"""Platform-admin flag on app_user.

A platform admin is a normal user row that may cross tenant boundaries on
the `/api/admin/*` routes only (firm + user provisioning for the operator).
Every other route stays strictly tenant-scoped and unchanged.

These accounts live in one dedicated "Fleek Operations" tenant, created and
populated by `tools.make_platform_admin` — never through the app.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("app_user", "is_platform_admin")

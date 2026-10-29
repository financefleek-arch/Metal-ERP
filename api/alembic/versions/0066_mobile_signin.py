"""Mobile sign-in: the mobile is the way in, so the email becomes optional.

`app_user.email` and `signup_request.email` accept NULL, and a mobile may belong to only one user across all
firms. Nothing existing changes: every user keeps the email they have and may still sign in with it.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as b:
        b.alter_column("email", existing_type=sa.String(200), nullable=True)
    with op.batch_alter_table("signup_request") as b:
        b.alter_column("email", existing_type=sa.String(200), nullable=True)
    op.create_index(
        "uq_user_mobile",
        "app_user",
        ["mobile"],
        unique=True,
        postgresql_where=sa.text("mobile IS NOT NULL"),
        sqlite_where=sa.text("mobile IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_mobile", table_name="app_user")
    # users made without an email cannot go back to NOT NULL: give them an address that cannot receive mail
    op.execute("UPDATE app_user SET email = id || '@no-email.invalid' WHERE email IS NULL")
    op.execute("UPDATE signup_request SET email = id || '@no-email.invalid' WHERE email IS NULL")
    with op.batch_alter_table("signup_request") as b:
        b.alter_column("email", existing_type=sa.String(200), nullable=False)
    with op.batch_alter_table("app_user") as b:
        b.alter_column("email", existing_type=sa.String(200), nullable=False)

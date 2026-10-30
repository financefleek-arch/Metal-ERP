"""Shorter sign-up: a password chosen after the first sign-in, and a call-back request from the code screen.

A sign-up may now start with only a firm name and a mobile. Its password is then chosen once the person is in
(`app_user.password_set` false until they do; they can always reset by WhatsApp code meanwhile). Existing users default
to true, so nothing changes for them. `callback_requested_at` is the "ask us to call me" tap on the code screen, which
reaches Fleek's Follow-ups queue through the lead feed.

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("password_set", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("signup_request", sa.Column("callback_requested_at", sa.DateTime(timezone=True)))
    op.alter_column("signup_request", "password_hash", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE signup_request SET password_hash = '' WHERE password_hash IS NULL")
    op.alter_column("signup_request", "password_hash", existing_type=sa.String(255), nullable=False)
    op.drop_column("signup_request", "callback_requested_at")
    op.drop_column("app_user", "password_set")

"""Sign-up, one-time codes, sign-in throttle and revocable sessions.

Existing firms and users are untouched: every new column is nullable or has a default that changes nothing
(token_version 0, must_change_password false, no verification stamps).

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as b:
        b.add_column(sa.Column("mobile", sa.String(20)))
        b.add_column(sa.Column("mobile_verified_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("token_version", sa.Integer, nullable=False, server_default="0"))
        b.add_column(
            sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True)))

    with op.batch_alter_table("tenant") as b:
        b.add_column(sa.Column("signup_source", sa.String(12)))
        b.add_column(sa.Column("onboarding_dismissed_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("feature_requests", sa.JSON().with_variant(JSONB(), "postgresql")))

    op.create_table(
        "signup_request",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("firm_name", sa.String(200), nullable=False),
        sa.Column("owner_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("mobile", sa.String(20), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("terms_version", sa.String(20), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant.id")),
        sa.Column("fleek_org_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_signup_request_email", "signup_request", ["email"])
    op.create_index("ix_signup_request_mobile", "signup_request", ["mobile"])

    op.create_table(
        "otp_challenge",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purpose", sa.String(12), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_otp_subject", "otp_challenge", ["purpose", "subject_id", "channel"])

    op.create_table(
        "auth_attempt",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_auth_attempt_lookup", "auth_attempt", ["kind", "key", "created_at"])


def downgrade() -> None:
    op.drop_table("auth_attempt")
    op.drop_table("otp_challenge")
    op.drop_table("signup_request")
    with op.batch_alter_table("tenant") as b:
        b.drop_column("feature_requests")
        b.drop_column("onboarding_dismissed_at")
        b.drop_column("signup_source")
    with op.batch_alter_table("app_user") as b:
        for c in (
            "terms_accepted_at",
            "must_change_password",
            "token_version",
            "email_verified_at",
            "mobile_verified_at",
            "mobile",
        ):
            b.drop_column(c)

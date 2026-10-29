"""Sign-up requests keep the Google Ads click id they came from.

Additive only: two nullable columns and an index. Nothing existing changes.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signup_request", sa.Column("ad_click_kind", sa.String(8)))
    op.add_column("signup_request", sa.Column("ad_click_id", sa.String(200)))
    op.create_index("ix_signup_request_ad_click_id", "signup_request", ["ad_click_id"])


def downgrade() -> None:
    op.drop_index("ix_signup_request_ad_click_id", table_name="signup_request")
    op.drop_column("signup_request", "ad_click_id")
    op.drop_column("signup_request", "ad_click_kind")

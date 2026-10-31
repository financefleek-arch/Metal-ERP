"""A sign-up request keeps where the visit came from.

`signup_request.attribution`: first-touch utm tags, referrer host, landing anchor, device and the os / browser /
in-app label read from the User-Agent (services/attribution.py). Fleek admin's Follow-ups page shows it. Older rows have none.

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signup_request", sa.Column("attribution", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade() -> None:
    op.drop_column("signup_request", "attribution")

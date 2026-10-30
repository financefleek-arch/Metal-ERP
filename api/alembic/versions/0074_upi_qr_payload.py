"""The shop's own UPI payment QR, kept as the text it encodes.

A shop uploads a picture of the QR from its payment app; the browser reads the `upi://pay?...` text out of it
and we store that text, never the picture. The bill then prints a clean QR made from it. Additive: one nullable
column.

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("upi_qr_payload", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant", "upi_qr_payload")

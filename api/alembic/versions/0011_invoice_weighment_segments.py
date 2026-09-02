"""Invoice weighment segments.

The shop's platform scale has a capacity ceiling. Goods are loaded on,
weighed, cleared, and the next batch loaded — each batch is one physical
weighment slip. The operator draws these boundaries in the editor by
tapping "Next segment" after N lines.

Storage is deliberately light (M1): `invoice_line.segment_no` numbers each
line's segment (1-based, default 1), and `invoice.weighment_slips` holds a
JSON list of the operator-recorded scale weights, one per closed segment:

    [{"seg": 1, "recorded_kg": "487.50"}, {"seg": 2, "recorded_kg": "264.50"}]

The *derived* weight/count totals come from the line `quantity` / `uom`
columns and are never stored — see `services.invoices.common`.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoice_line",
        sa.Column("segment_no", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "invoice",
        sa.Column("weighment_slips", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoice", "weighment_slips")
    op.drop_column("invoice_line", "segment_no")

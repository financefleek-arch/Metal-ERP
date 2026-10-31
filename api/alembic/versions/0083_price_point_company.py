"""A bill's price point remembers the Tally company it was paid in (S6.6).

`supplier_price_point.tally_company_id`; a quote (a price list) belongs to no company. Backfill: a
price from a bill takes the bill's company (set by 0082 for approved bills).

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("supplier_price_point", sa.Column("tally_company_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_price_point_company", "supplier_price_point", "tally_company", ["tally_company_id"], ["id"]
    )
    op.create_index("ix_price_point_company", "supplier_price_point", ["tally_company_id"])
    op.execute(
        "UPDATE supplier_price_point SET tally_company_id = ("
        "SELECT b.tally_company_id FROM inward_bill b WHERE b.id = supplier_price_point.source_id) "
        "WHERE source = 'bill'"
    )


def downgrade() -> None:
    op.drop_index("ix_price_point_company", table_name="supplier_price_point")
    op.drop_constraint("fk_price_point_company", "supplier_price_point", type_="foreignkey")
    op.drop_column("supplier_price_point", "tally_company_id")

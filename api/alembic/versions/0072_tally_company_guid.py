"""Remember each Tally company's own id and books-from date.

The company name on `tally_company` is text we typed; a rename in Tally, or a different company
opened under the same name, would silently point our links at the wrong books. The GUID is what
survives a rename. Additive: five nullable columns, null until "Verify company" runs.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tally_company", sa.Column("company_guid", sa.String(64), nullable=True))
    op.add_column("tally_company", sa.Column("books_from", sa.Date(), nullable=True))
    op.add_column("tally_company", sa.Column("verify_state", sa.String(16), nullable=True))
    op.add_column("tally_company", sa.Column("verify_note", sa.String(400), nullable=True))
    op.add_column(
        "tally_company", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    for col in ("verified_at", "verify_note", "verify_state", "books_from", "company_guid"):
        op.drop_column("tally_company", col)

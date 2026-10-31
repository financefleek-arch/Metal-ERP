"""Documents carry their Tally company (S2.1, S2.2, S2.6).

`invoice.tally_company_id` and `inward_bill.tally_company_id` say which company a bill posts to;
`party.default_company_id` is a customer's default. Backfill: finalized invoices and approved bills go
to their firm's default company (a firm had one until 0075), so nothing already pushed changes; drafts
stay null and take the default when they are finalized.

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT = "(SELECT c.id FROM tally_company c WHERE c.tenant_id = {t}.tenant_id AND c.is_default)"


def _col(table: str) -> str:
    return "default_company_id" if table == "party" else "tally_company_id"


def upgrade() -> None:
    for table in ("invoice", "inward_bill", "party"):
        col = _col(table)
        op.add_column(table, sa.Column(col, sa.String(36), nullable=True))
        op.create_foreign_key(f"fk_{table}_{col}", table, "tally_company", [col], ["id"])
        op.create_index(f"ix_{table}_{col}", table, [col])
    op.execute(
        f"UPDATE invoice SET tally_company_id = {_DEFAULT.format(t='invoice')} WHERE status = 'final'"
    )
    op.execute(
        f"UPDATE inward_bill SET tally_company_id = {_DEFAULT.format(t='inward_bill')} "
        "WHERE status = 'approved'"
    )


def downgrade() -> None:
    for table in ("invoice", "inward_bill", "party"):
        col = _col(table)
        op.drop_index(f"ix_{table}_{col}", table_name=table)
        op.drop_constraint(f"fk_{table}_{col}", table, type_="foreignkey")
        op.drop_column(table, col)

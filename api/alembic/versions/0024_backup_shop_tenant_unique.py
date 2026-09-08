"""One companion-agent install per firm.

`backup_shop.tenant_id` has always existed but was an informational soft
link. The Ops console now provisions the agent *from the firm's own page*
(`POST /api/admin/firms/{id}/agent`), so a firm has exactly one agent and
`tally_company.shop_id` / every future shop-side capability keys off it.

Partial unique index (`WHERE tenant_id IS NOT NULL`) so legacy CLI-created
shops with a null tenant_id are untouched. Both Postgres and SQLite (tests,
>= 3.8) support partial indexes.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_backup_shop_tenant",
        "backup_shop",
        ["tenant_id"],
        unique=True,
        postgresql_where="tenant_id IS NOT NULL",
        sqlite_where="tenant_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_backup_shop_tenant", table_name="backup_shop")

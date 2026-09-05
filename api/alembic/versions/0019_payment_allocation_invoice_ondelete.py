"""payment_allocation.invoice_id -> ON DELETE SET NULL.

Deleting an invoice is only ever reachable once it's a draft or has been
cancelled (finalized invoices can't be deleted directly). A cancelled
invoice that still has a *posted* payment allocated against it is blocked
at the application layer (see routers/invoices.py::delete_invoice) — that
check is unaffected by this migration.

But a *reversed* payment's allocation row is kept forever on purpose (the
audit trail — see 0017's docstring), so "any allocation row exists" can't
be the delete gate, or a reversed payment would permanently pin its
invoice even though "reverse it first" is the whole point of reversal.
Without an ON DELETE rule, the raw DELETE would still fail at the database
with a bare FK-violation the moment only reversed allocations remain.

ON DELETE SET NULL lets that DELETE succeed: a reversed allocation whose
invoice is later deleted just loses the invoice_id it pointed to (its
`type` stays 'against_invoice' for its own history, amount/payment record
untouched) — never CASCADE, since payment_allocation rows themselves are
never deleted by this app.

The original FK (0017) was created via a bare `sa.ForeignKeyConstraint(...)`
with no explicit `name=`, so its actual constraint name is whatever the
DB auto-assigned — reflected at runtime here rather than guessed, since a
hardcoded guess could silently mismatch the real Postgres-assigned name
(this project runs SQLite in tests, Postgres in prod; batch mode covers
SQLite's "recreate the table" ALTER limitation, Postgres uses a plain
ALTER).

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_FK_NAME = "fk_payment_allocation_invoice_id_ondelete_set_null"


def _existing_invoice_fk_name(conn: sa.engine.Connection) -> str | None:
    inspector = sa.inspect(conn)
    for fk in inspector.get_foreign_keys("payment_allocation"):
        if fk.get("referred_table") == "invoice" and fk.get("constrained_columns") == [
            "invoice_id"
        ]:
            return fk.get("name")
    return None


def upgrade() -> None:
    conn = op.get_bind()
    existing_name = _existing_invoice_fk_name(conn)
    with op.batch_alter_table("payment_allocation") as batch_op:
        if existing_name:
            batch_op.drop_constraint(existing_name, type_="foreignkey")
        batch_op.create_foreign_key(
            _NEW_FK_NAME,
            "invoice",
            ["invoice_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("payment_allocation") as batch_op:
        batch_op.drop_constraint(_NEW_FK_NAME, type_="foreignkey")
        batch_op.create_foreign_key(
            "payment_allocation_invoice_id_fkey",
            "invoice",
            ["invoice_id"],
            ["id"],
        )

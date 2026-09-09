"""F5d: purchase-voucher-out — converge tally_ledger_config into
tally_company.ledger_map.

Data-only migration: for every tenant that has a `tally_ledger_config`
row AND a `tally_company` row, merge `purchase_ledger` (and
`creditors_group`, under the `creditors_parent` key `ledger_map` already
uses) into that company's `ledger_map` JSON, only filling keys that are
still absent — an operator-entered `ledger_map` value always wins over
the old inward-only config. No column changes. `tally_ledger_config`
itself is left in place (not dropped) — `approve.py` still reads it as a
fallback for tenants with no `tally_company` yet, keeping the pre-F5d
file-export path working unchanged; dropping the table is a follow-up
migration once nothing references it, per the F5d execution plan.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-09
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.text(
            """
            SELECT c.id AS company_id, c.ledger_map,
                   lc.purchase_ledger, lc.creditors_group
            FROM tally_company c
            JOIN tally_ledger_config lc ON lc.tenant_id = c.tenant_id
            """
        )
    ).fetchall()

    for row in rows:
        ledger_map = row.ledger_map or {}
        if isinstance(ledger_map, str):  # SQLite plain-JSON column, defensive
            ledger_map = json.loads(ledger_map) if ledger_map else {}
        changed = False
        if not ledger_map.get("purchase_ledger") and row.purchase_ledger:
            ledger_map["purchase_ledger"] = row.purchase_ledger
            changed = True
        if not ledger_map.get("creditors_parent") and row.creditors_group:
            ledger_map["creditors_parent"] = row.creditors_group
            changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE tally_company SET ledger_map = :lm WHERE id = :id"),
                {"lm": json.dumps(ledger_map), "id": row.company_id},
            )


def downgrade() -> None:
    # Data-only forward merge; no reliable inverse (we don't track which
    # keys we added vs. which the operator already had). No-op.
    pass

"""Ledger names live on the Tally company only (S1.5).

Copies each firm's `tally_ledger_config` row into its default company's `ledger_map`, for every key the
map lacks (0028 copied only purchase and creditors). A key the map already has with a different value is
NOT overwritten and is printed, so the owner can see the disagreement. The legacy table stays for firms
with no company yet and is dropped in a later release.

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-31
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYS = {
    "creditors_group": "creditors_parent",
    "purchase_ledger": "purchase_ledger",
    "cgst_ledger": "cgst_ledger",
    "sgst_ledger": "sgst_ledger",
    "igst_ledger": "igst_ledger",
    "round_off_ledger": "round_off_ledger",
}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT c.id AS company_id, c.tenant_id, c.ledger_map, lc.* FROM tally_company c "
            "JOIN tally_ledger_config lc ON lc.tenant_id = c.tenant_id WHERE c.is_default"
        )
    ).mappings().all()
    for row in rows:
        ledger_map = row["ledger_map"] or {}
        if isinstance(ledger_map, str):
            ledger_map = json.loads(ledger_map) if ledger_map else {}
        changed = False
        for column, key in _KEYS.items():
            legacy = row[column]
            if not legacy:
                continue
            if not ledger_map.get(key):
                ledger_map[key] = legacy
                changed = True
            elif ledger_map[key] != legacy:
                print(  # noqa: T201 - migration output, for the operator
                    f"[0080] tenant {row['tenant_id']}: {key} is {ledger_map[key]!r} on the company, "
                    f"{legacy!r} in the old config; kept the company's"
                )
        if changed:
            bind.execute(
                sa.text("UPDATE tally_company SET ledger_map = :lm WHERE id = :id"),
                {"lm": json.dumps(ledger_map), "id": row["company_id"]},
            )


def downgrade() -> None:
    pass  # data-only forward merge

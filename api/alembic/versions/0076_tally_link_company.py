"""A record has one Tally link per Tally company (S1.2).

`tally_link` gains `company_id`; its two uniqueness rules become per company, so the same customer or
item can be linked in the GST books and the estimate book under different Tally ids.

Backfill: every existing link belongs to its firm's default company (a firm had exactly one before
0075). Customers and items that only carry a Tally id on the record itself (`party.tally_guid`,
`item.tally_guid`) get a link in that default company, so nothing reads the old columns any more:
where two records of a firm hold the same id, the oldest keeps it.

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tally_link", sa.Column("company_id", sa.String(36), nullable=True))
    op.execute(
        "UPDATE tally_link SET company_id = ("
        "SELECT c.id FROM tally_company c "
        "WHERE c.tenant_id = tally_link.tenant_id AND c.is_default)"
    )
    # A link is only ever made from a job on a company, so none should be left without one; any that
    # is could not be read by anything.
    op.execute("DELETE FROM tally_link WHERE company_id IS NULL")
    op.alter_column("tally_link", "company_id", nullable=False)
    op.create_foreign_key(
        "fk_tally_link_company", "tally_link", "tally_company", ["company_id"], ["id"]
    )
    op.create_index("ix_tally_link_company_id", "tally_link", ["company_id"])

    op.drop_constraint("uq_tally_link_entity", "tally_link", type_="unique")
    op.drop_constraint("uq_tally_link_guid", "tally_link", type_="unique")
    op.create_unique_constraint(
        "uq_tally_link_entity", "tally_link", ["company_id", "entity_type", "entity_id"]
    )
    op.create_unique_constraint(
        "uq_tally_link_guid", "tally_link", ["company_id", "entity_type", "tally_guid"]
    )

    if op.get_bind().dialect.name != "postgresql":
        return  # the backfill below uses Postgres syntax; a fresh SQLite database has no data to move
    for entity_type, table in (("party", "party"), ("item", "item")):
        op.execute(
            f"""
            INSERT INTO tally_link (id, tenant_id, company_id, entity_type, entity_id, tally_guid, created_at)
            SELECT DISTINCT ON (c.id, e.tally_guid)
                   gen_random_uuid()::text, e.tenant_id, c.id, '{entity_type}', e.id, e.tally_guid, now()
            FROM {table} e
            JOIN tally_company c ON c.tenant_id = e.tenant_id AND c.is_default
            WHERE e.tally_guid IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM tally_link l WHERE l.entity_type = '{entity_type}' AND l.entity_id = e.id)
              AND NOT EXISTS (
                  SELECT 1 FROM tally_link l2
                  WHERE l2.company_id = c.id AND l2.entity_type = '{entity_type}'
                    AND l2.tally_guid = e.tally_guid)
            ORDER BY c.id, e.tally_guid, e.created_at
            """
        )


def downgrade() -> None:
    # Links of a second company would collide with the old per-firm rules; keep the default company's.
    op.execute(
        "DELETE FROM tally_link WHERE company_id NOT IN "
        "(SELECT id FROM tally_company WHERE is_default)"
    )
    op.drop_constraint("uq_tally_link_guid", "tally_link", type_="unique")
    op.drop_constraint("uq_tally_link_entity", "tally_link", type_="unique")
    op.create_unique_constraint(
        "uq_tally_link_entity", "tally_link", ["tenant_id", "entity_type", "entity_id"]
    )
    op.create_unique_constraint(
        "uq_tally_link_guid", "tally_link", ["tenant_id", "entity_type", "tally_guid"]
    )
    op.drop_index("ix_tally_link_company_id", table_name="tally_link")
    op.drop_constraint("fk_tally_link_company", "tally_link", type_="foreignkey")
    op.drop_column("tally_link", "company_id")

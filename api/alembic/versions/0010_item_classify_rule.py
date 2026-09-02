"""item_classify_rule — per-tenant learned (phrase -> group) rules for the
rules-first item classifier. Written when a user recategorises an
unconfirmed item; read by classify_item on every create path.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_classify_rule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(36), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column("phrase_normalized", sa.String(120), nullable=False),
        sa.Column("department", sa.String(60), nullable=False),
        sa.Column(
            "group_id", sa.String(36), sa.ForeignKey("product_group.id"), nullable=True
        ),
        sa.Column("source", sa.String(20), server_default="learned", nullable=False),
        sa.Column("hits", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "phrase_normalized", name="uq_classify_rule_tenant_phrase"
        ),
    )
    op.create_index(
        "ix_item_classify_rule_tenant_id", "item_classify_rule", ["tenant_id"]
    )
    op.create_index(
        "ix_item_classify_rule_group_id", "item_classify_rule", ["group_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_item_classify_rule_group_id", table_name="item_classify_rule")
    op.drop_index("ix_item_classify_rule_tenant_id", table_name="item_classify_rule")
    op.drop_table("item_classify_rule")

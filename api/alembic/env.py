"""Alembic environment.

Pulls the DB URL from app.config (single source of truth, no credentials
in a tracked file) and targets app.db.Base.metadata for autogenerate.
Importing app.models registers every model on that metadata.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (registers all models on Base.metadata)
from alembic import context
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata

# Postgres-only indexes that exist in the migrations but are deliberately NOT declared on the models:
# pg_trgm GIN indexes, expression indexes and CONCURRENTLY-built browse indexes (0002, 0003, 0015,
# 0023). The ORM can't express most of them, and without this `alembic check` reports each one as
# "removed". Add a name here when a migration creates a hand-written index of the same kind.
MIGRATION_ONLY_INDEXES = frozenset(
    {
        "ix_item_name_normalized_trgm",
        "ix_item_alias_normalized_trgm",
        "ix_item_tenant_browse",
        "ix_item_name_lower_trgm",
        "ix_party_legal_name_trgm",
        "ix_party_tenant_browse",
        "ix_party_legal_name_lower_trgm",
        "ix_party_namekey_trgm",
        "ix_party_tenant_namekey",
    }
)


def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    return not (type_ == "index" and name in MIGRATION_ONLY_INDEXES)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

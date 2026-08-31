"""Test fixtures.

Uses an in-memory SQLite DB with the full metadata created directly (not
via Alembic) — fast, isolated per test session. The Postgres-specific
bits (JSONB, pg_trgm) degrade to portable equivalents via the model
`.with_variant` declarations, so schema-shape tests still pass here; the
real Postgres path is exercised by CI's service container.
"""

from __future__ import annotations

import os

# Point the app engine at a throwaway file DB before app.db / app.config
# import — keeps /health and any engine-touching code off a real Postgres
# in tests and CI.
os.environ.setdefault("DATABASE_URL", "sqlite:///./_pytest.db")
os.environ.setdefault("APP_ENV", "test")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: F401,E402  (registers models)
from app.db import Base  # noqa: E402
from app.db import engine as app_engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_app_engine_schema() -> Iterator[None]:
    """The module-level app engine (used by /health) needs its schema too."""
    Base.metadata.create_all(app_engine)
    yield
    Base.metadata.drop_all(app_engine)


@pytest.fixture(scope="session")
def engine():  # type: ignore[no-untyped-def]
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    """A transaction-wrapped session, rolled back after each test."""
    conn = engine.connect()
    trans = conn.begin()
    SessionTest = sessionmaker(bind=conn, expire_on_commit=False)
    s = SessionTest()
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()

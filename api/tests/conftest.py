"""Test fixtures.

All tests run against a single throwaway SQLite **file** DB, which is the
same engine `app.db` builds (env var set below, before app.db imports).
Postgres-specific types (JSONB) degrade to portable equivalents via the
model `.with_variant` declarations; the real Postgres path is exercised
by CI's service container.

Isolation is per-test table wipe (autouse `_clean_db`), not a rolled-back
transaction — because the API routes open their own request-scoped
sessions via `get_session` and commit, so a test that also touches the DB
directly must share the committed state, not a separate transaction.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./_pytest.db")
os.environ.setdefault("APP_ENV", "test")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import app.models  # noqa: F401,E402  (registers models on Base.metadata)
from app.db import Base  # noqa: E402
from app.db import engine as app_engine  # noqa: E402

_SessionTest = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.create_all(app_engine)
    yield
    Base.metadata.drop_all(app_engine)


@pytest.fixture(autouse=True)
def _clean_db() -> Iterator[None]:
    """Empty every table before each test (FK-safe reverse order)."""
    with app_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


@pytest.fixture
def session() -> Iterator[Session]:
    """A plain session on the app engine. Commit in the test if the app
    needs to see the write on a later request.
    """
    s = _SessionTest()
    try:
        yield s
        s.commit()
    finally:
        s.close()

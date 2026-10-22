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
import tempfile

os.environ.setdefault("DATABASE_URL", "sqlite:///./_pytest.db")
os.environ.setdefault("APP_ENV", "test")

# File outputs default to /data/... (a mounted volume in prod). That path is not writable on a CI
# runner, and on a dev box it litters a real C:\data, so tests write to a throwaway directory.
_TEST_FILES = tempfile.mkdtemp(prefix="metalerp-test-")
for _var, _sub in (
    ("PDF_DIR", "pdfs"),
    ("INWARD_DIR", "inward"),
    ("CATALOG_LOCAL_DIR", "catalog"),
    ("TALLY_AGENT_BUILD_DIR", "tally-agent-build"),
):
    os.environ.setdefault(_var, os.path.join(_TEST_FILES, _sub))

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import app.models  # noqa: F401,E402  (registers models on Base.metadata)
from app.db import Base  # noqa: E402
from app.db import engine as app_engine  # noqa: E402


@event.listens_for(app_engine, "connect")
def _enforce_foreign_keys(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    """SQLite ignores foreign keys unless asked: Postgres (production) does not, so a delete that
    leaves a reference behind must fail here too."""
    if app_engine.dialect.name == "sqlite":
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


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


@pytest.fixture(autouse=True)
def _suite_assumes_no_fuzzy(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the trigram (fuzzy) rung out of the party/item resolvers unless a test asks for it.

    The suite was written against SQLite, where that rung does not exist. On Postgres it fires:
    "Supplier One" / "Supplier Two" count as near-duplicates, the create route answers 409, and every
    test that just wants two suppliers breaks. Tests that exist to check the fuzzy rung carry
    `@pytest.mark.pg_fuzzy` and run it for real (they skip on SQLite).
    """
    if request.node.get_closest_marker("pg_fuzzy"):
        return
    from app.services import item_resolution, party_resolution

    monkeypatch.setattr(party_resolution, "_is_postgres", lambda session: False)
    monkeypatch.setattr(item_resolution, "_is_postgres", lambda session: False)


@pytest.fixture(autouse=True)
def _no_real_meta_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test must never reach Meta. One that forgot to stub a send used to pass locally (no PDF
    renderer) and fail in CI with a real 401 from graph.facebook.com; now it fails the same way
    everywhere, naming the call. Tests that patch `httpx.post` themselves override this."""
    import httpx

    real_post = httpx.post

    def guarded(url: str, *args: object, **kwargs: object) -> httpx.Response:
        if "graph.facebook.com" in str(url):
            raise AssertionError(f"test tried to call Meta for real: POST {url}")
        return real_post(url, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "post", guarded)


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

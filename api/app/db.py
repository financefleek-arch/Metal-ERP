"""Database engine, session factory, and the declarative base.

One engine per process. `get_session` is the FastAPI dependency; every
request gets its own session, committed on success and rolled back on
exception, then closed.
"""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True, "echo": False}
if _settings.database_url.startswith("postgresql"):
    # A shared Postgres instance — keep the pool modest.
    _engine_kwargs.update(pool_size=5, max_overflow=5)

engine = create_engine(_settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a session, commits on success."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

"""FastAPI application entry point.

Milestone 1 exposes only /health so the container can be built and
deployed live before the domain routes (party, item, invoice) land.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

settings = get_settings()

app = FastAPI(
    title="Metal ERP API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness + DB reachability. A real SELECT 1 through the pool, so a
    DB-disconnected-but-still-listening process reports unhealthy rather
    than healthy (matches fleek-backend's /health contract).
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "env": settings.app_env}

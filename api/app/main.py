"""FastAPI application entry point.

Everything is mounted under /api so a single reverse-proxy rule
(`handle /api/*`) covers the whole backend and the rest of the host
serves the SPA.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.routers import (
    auth,
    inward,
    items,
    parties,
    parties_import,
    reference,
    tenant,
)

settings = get_settings()

app = FastAPI(
    title="Metal ERP API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# In production the SPA is same-origin (served by Caddy at the same host),
# so CORS is a dev-only convenience for `vite dev` on :5173.
if not settings.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(reference.router)
app.include_router(tenant.router)
app.include_router(parties.router)
app.include_router(parties_import.router)
app.include_router(items.router)
app.include_router(inward.router)
if not settings.is_production:
    # Dev-only: PDF-in / XML-out, no auth, for quick Tally-import testing.
    # Imported here (not at module top) so this dev tool can never affect
    # a production boot.
    from app.routers import inward_debug

    app.include_router(inward_debug.router)


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness + DB reachability. A real SELECT 1 through the pool, so a
    DB-disconnected-but-still-listening process reports unhealthy rather
    than healthy. Exposed at both /health (internal Docker healthcheck,
    hits the container directly) and /api/health (through the proxy).
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "env": settings.app_env}

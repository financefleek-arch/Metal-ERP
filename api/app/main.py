"""FastAPI application entry point.

Everything is mounted under /api so a single reverse-proxy rule
(`handle /api/*`) covers the whole backend and the rest of the host
serves the SPA.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.routers import (
    admin,
    auth,
    catalog,
    catalog_review,
    catalog_tally,
    customer_catalogs,
    documents,
    invoices,
    inward,
    item_categories,
    item_groups,
    item_labels,
    item_sheet,
    item_tally,
    items,
    items_import,
    jobs,
    media,
    parties,
    parties_import,
    payments,
    reference,
    tally,
    tally_agent,
    tally_self_serve,
    tenant,
    whatsapp,
)

settings = get_settings()

log = logging.getLogger("app")
SWEEP_EVERY_SECONDS = 24 * 60 * 60


def _sweep_photos() -> None:
    """Delete photos nothing refers to any more, for every firm."""
    from app.db import SessionLocal
    from app.services import media as media_svc
    from app.services.catalog.storage import get_storage

    try:
        storage = get_storage()
    except Exception:  # noqa: BLE001 - storage not set up here: nothing to sweep
        return
    with SessionLocal() as s:
        removed, freed = media_svc.sweep_orphans(s, storage)
        s.commit()
    if removed:
        log.info("photo sweep: %d images removed, %d bytes freed", removed, freed)


async def _sweep_loop() -> None:
    await asyncio.sleep(300)  # let the app settle after a deploy
    while True:
        try:
            await asyncio.to_thread(_sweep_photos)
        except Exception:  # noqa: BLE001 - never let housekeeping stop the loop
            log.exception("photo sweep failed")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = None if settings.app_env == "test" else asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(
    lifespan=lifespan,
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
app.include_router(admin.router)
app.include_router(reference.router)
app.include_router(tenant.router)
app.include_router(parties.router)
app.include_router(parties_import.router)
app.include_router(items_import.router)
app.include_router(items.router)
app.include_router(item_categories.router)
app.include_router(item_groups.router)
app.include_router(invoices.router)
app.include_router(payments.router)
app.include_router(payments.collections_router)
app.include_router(payments.ledger_router)
app.include_router(whatsapp.router)
app.include_router(whatsapp.invoice_router)
app.include_router(tally.router)
app.include_router(tally_agent.router)
app.include_router(tally_self_serve.router)
app.include_router(inward.router)
app.include_router(catalog.router)
app.include_router(catalog_tally.router)
app.include_router(catalog_review.router)
app.include_router(customer_catalogs.router)
app.include_router(documents.router)
app.include_router(item_labels.router)
app.include_router(item_sheet.router)
app.include_router(item_tally.router)
app.include_router(jobs.router)
app.include_router(media.router)
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

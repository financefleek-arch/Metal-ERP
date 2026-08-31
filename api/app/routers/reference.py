"""Read-only reference data for the frontend (state codes, etc.)."""

from __future__ import annotations

from fastapi import APIRouter

from app.reference import STATE_CODES

router = APIRouter(prefix="/api/reference", tags=["reference"])


@router.get("/states")
def states() -> list[dict[str, str]]:
    """GST state codes, sorted by name — for the party/firm state picker."""
    return sorted(
        ({"code": code, "name": name} for code, name in STATE_CODES.items()),
        key=lambda s: s["name"],
    )

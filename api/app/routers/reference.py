"""Read-only reference data for the frontend: state codes, HSN lookup,
and the metal-trade vocabularies (UOM / category / shape / metal / finish).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.deps import CurrentUser, SessionDep
from app.models import HsnCode
from app.reference import STATE_CODES

router = APIRouter(prefix="/api/reference", tags=["reference"])

# Small fixed vocabularies — code lists, not DB tables. The item form uses
# these for its pickers; free text is still allowed on save.
UOMS: list[str] = [
    "kg", "mt", "quintal", "nos", "pcs", "set", "pair", "bundle",
    "coil", "sheet", "length", "ft", "m", "sqft", "sqm", "ltr",
]
CATEGORIES: list[str] = [
    "Stainless flat", "Stainless long", "Stainless pipe",
    "MS flat", "MS long", "MS structural", "MS pipe",
    "GI / galvanised", "Aluminium flat", "Aluminium section",
    "Brass / copper", "Cast iron", "Fasteners & fittings",
    "Utensils", "Hardware", "Scrap", "Other",
]
SHAPES: list[str] = [
    "angle", "channel", "beam", "flat", "patta", "round_bar", "square_bar",
    "sheet", "plate", "coil", "pipe", "tube", "wire", "rod", "ingot",
    "scrap", "utensil", "fitting", "other",
]
METALS: list[str] = [
    "MS", "SS", "GI", "aluminium", "brass", "copper", "cast_iron", "other",
]
FINISHES: list[str] = [
    "mill", "polished", "matte", "brushed", "galvanised", "pvc_coated", "painted",
]


@router.get("/states")
def states() -> list[dict[str, str]]:
    """GST state codes, sorted by name — for the party/firm state picker."""
    return sorted(
        ({"code": code, "name": name} for code, name in STATE_CODES.items()),
        key=lambda s: s["name"],
    )


@router.get("/uoms")
def uoms() -> list[str]:
    return UOMS


@router.get("/categories")
def categories() -> list[str]:
    return CATEGORIES


@router.get("/shapes")
def shapes() -> list[str]:
    return SHAPES


@router.get("/metals")
def metals() -> list[str]:
    return METALS


@router.get("/finishes")
def finishes() -> list[str]:
    return FINISHES


@router.get("/hsn")
def hsn_lookup(
    _user: CurrentUser,
    session: SessionDep,
    q: str = Query(default="", description="code prefix or description words"),
    limit: int = Query(default=20, le=50),
) -> list[dict[str, object]]:
    """Search the shipped HSN reference by code prefix or description text."""
    stmt = select(HsnCode)
    term = q.strip().lower()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                HsnCode.code.like(f"{term}%"),
                func.lower(HsnCode.description).like(like),
            )
        )
    stmt = stmt.order_by(HsnCode.code).limit(limit)
    return [
        {
            "code": h.code,
            "description": h.description,
            "gst_rate": float(h.default_gst_rate) if h.default_gst_rate is not None else None,
        }
        for h in session.scalars(stmt).all()
    ]

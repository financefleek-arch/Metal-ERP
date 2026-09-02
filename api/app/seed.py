"""Idempotent seed for a fresh Metal ERP database.

Run after `alembic upgrade head`:

    python -m app.seed

Seeds:
  - HSN reference codes (metal-trade subset)
  - name-normalization synonyms
  - one tenant + one owner user  (only if TENANT_* / ADMIN_* env vars set)

Safe to re-run: every insert checks for an existing row first.
"""

from __future__ import annotations

import os

from passlib.hash import argon2
from sqlalchemy import select

from app.db import SessionLocal
from app.models import HsnCode, Synonym, Tenant, User
from app.models._mixins import UserRole

# --- HSN subset relevant to steel / aluminium / iron / brass / utensils ---
HSN_CODES: list[tuple[str, str, str, float]] = [
    ("72061000", "Iron/non-alloy steel in ingots", "72", 18.0),
    ("72071110", "Semi-finished iron/non-alloy steel, billets", "72", 18.0),
    ("72131010", "Bars/rods, hot-rolled coils, iron/non-alloy steel", "72", 18.0),
    ("72142000", "Bars/rods of iron/non-alloy steel, deformed (TMT)", "72", 18.0),
    ("72161000", "U/I/H sections of iron/non-alloy steel, < 80 mm", "72", 18.0),
    ("72169910", "Angles, shapes and sections of iron/steel, other", "72", 18.0),
    ("72085110", "Flat-rolled iron/steel, >= 600 mm, > 10 mm thick", "72", 18.0),
    ("72104900", "Flat-rolled iron/steel, zinc-coated (GP/GC sheet)", "72", 18.0),
    ("72193590", "Flat-rolled stainless steel, >= 600 mm, cold, < 3 mm", "72", 18.0),
    ("72202029", "Flat-rolled stainless steel, < 600 mm, cold-rolled", "72", 18.0),
    ("73063090", "Tubes/pipes, welded, circular, iron/steel, other", "73", 18.0),
    ("73066900", "Tubes/pipes, welded, non-circular, other alloy steel", "73", 18.0),
    ("73084000", "Equipment for scaffolding, shuttering, propping", "73", 18.0),
    ("73089090", "Structures and parts of structures, iron/steel", "73", 18.0),
    ("73170000", "Nails, tacks, staples and similar, of iron/steel", "73", 18.0),
    ("73181500", "Threaded bolts and screws, of iron/steel, other", "73", 18.0),
    ("73239310", "SS household articles, pressure cookers", "73", 12.0),
    ("73239390", "SS household/kitchen articles, other", "73", 12.0),
    ("73239990", "Iron/steel household/kitchen articles, other", "73", 12.0),
    ("74040022", "Copper/copper-alloy scrap, brass scrap", "74", 18.0),
    ("74091900", "Plates/sheets/strip of refined copper, coils", "74", 18.0),
    ("74122000", "Copper alloy tube or pipe fittings", "74", 18.0),
    ("74181021", "Brass household/kitchen articles, utensils", "74", 12.0),
    ("76011000", "Unwrought aluminium, not alloyed", "76", 18.0),
    ("76012010", "Unwrought aluminium alloys, ingots", "76", 18.0),
    ("76041000", "Bars, rods and profiles of non-alloy aluminium", "76", 18.0),
    ("76042910", "Bars and rods of aluminium alloys", "76", 18.0),
    ("76061190", "Plates/sheets/strip, non-alloy aluminium, rect.", "76", 18.0),
    ("76061200", "Plates/sheets/strip of aluminium alloys, rect.", "76", 18.0),
    ("76069210", "Plates/sheets/strip, aluminium alloys, non-rect. (patta)", "76", 18.0),
    ("76071110", "Aluminium foil, not backed, < 0.2 mm", "76", 18.0),
    ("76151030", "Aluminium household articles, pressure cookers", "76", 12.0),
    ("76151040", "Aluminium household/kitchen articles, utensils", "76", 12.0),
    ("72042190", "Ferrous waste and scrap, stainless steel, other", "72", 18.0),
    ("72044900", "Ferrous waste and scrap, other", "72", 18.0),
]

# --- token rewrites for item-name normalization ---
SYNONYMS: list[tuple[str, str]] = [
    ("stainless", "ss"),
    ("s s", "ss"),
    ("s.s", "ss"),
    ("sus", "ss"),
    ("mild", "ms"),
    ("m s", "ms"),
    ("aluminum", "aluminium"),
    ("alu", "aluminium"),
    ("al", "aluminium"),
    ("alum", "aluminium"),
    ("patti", "patta"),
    ("pcs", "nos"),
    ("pc", "nos"),
    ("piece", "nos"),
    ("pieces", "nos"),
    ("no", "no"),
    ("number", "no"),
    ("kgs", "kg"),
    ("kilo", "kg"),
    ("kgm", "kg"),
    ("mtr", "m"),
    ("meter", "m"),
    ("metre", "m"),
    ("mm.", "mm"),
    ("inch", "in"),
    ("dia", "dia"),
    ("thk", "thick"),
    ("sq", "square"),
    ("rnd", "round"),
    ("angl", "angle"),
    ("chnl", "channel"),
    ("sht", "sheet"),
]

# --- bartan (utensil) trade vocabulary ---
# HINDI-canonical: the shop names things in Hindi ("MOR JHULA", "AL BALTI"),
# so a Hindi word is the canonical token and only *spelling variants*
# collapse onto it. We deliberately do NOT rewrite a Hindi word to an
# English one (that mangles the shop's own catalogue), and NEVER rewrite a
# brand token ("prestige", "hawkins") to a product type.
#
# Cross-language recall (an English-speaking customer types "bucket") is a
# job for the `item_alias` learning loop, not this table.
#
# All rows are many-to-one by design (`normalize_name` supports it).
BARTAN_SYNONYMS: list[tuple[str, str]] = [
    # --- spelling variants -> canonical Hindi spelling ---
    ("jhoola", "jhula"),
    ("zhula", "jhula"),
    ("zula", "jhula"),
    ("kadhai", "kadai"),
    ("kadahi", "kadai"),
    ("karahi", "kadai"),
    ("karai", "kadai"),
    ("kadhaai", "kadai"),
    ("pateela", "patila"),
    ("bhagauna", "bhagona"),
    ("gilaas", "gilas"),
    ("gilass", "gilas"),
    ("chammach", "chamcha"),
    ("chhalni", "chalni"),
    ("channi", "chalni"),
    ("saancha", "sancha"),
    ("paraat", "parat"),
    ("dhakni", "dhakkan"),
    ("peetal", "pital"),
    ("pittal", "pital"),
    ("tanba", "tamba"),
    ("taamba", "tamba"),
    ("kaansa", "kansa"),
    ("degcha", "degchi"),
    ("degacha", "degchi"),
    # (metal shorthand — "stainless"->"ss", "aluminum"->"aluminium" etc. —
    #  is already covered by the metal-bar SYNONYMS block above.)
    # --- unit / pack noise on utensil bills ---
    ("nag", "nos"),
    ("nug", "nos"),
    ("jodi", "pair"),
    ("darjan", "dozen"),
    ("doz", "dozen"),
    ("dz", "dozen"),
]

SYNONYMS = SYNONYMS + BARTAN_SYNONYMS


def seed_hsn(session) -> int:  # type: ignore[no-untyped-def]
    existing = set(session.scalars(select(HsnCode.code)).all())
    added = 0
    for code, desc, chapter, rate in HSN_CODES:
        if code in existing:
            continue
        session.add(
            HsnCode(code=code, description=desc, chapter=chapter, default_gst_rate=rate)
        )
        added += 1
    return added


def seed_synonyms(session, tenant_id: str) -> int:  # type: ignore[no-untyped-def]
    existing = set(
        session.scalars(
            select(Synonym.from_token).where(Synonym.tenant_id == tenant_id)
        ).all()
    )
    added = 0
    for frm, to in SYNONYMS:
        if frm in existing:
            continue
        session.add(Synonym(tenant_id=tenant_id, from_token=frm, to_token=to))
        added += 1
    return added


def seed_tenant_and_admin(session) -> str | None:  # type: ignore[no-untyped-def]
    name = os.environ.get("SEED_TENANT_NAME")
    admin_email = os.environ.get("SEED_ADMIN_EMAIL")
    admin_pass = os.environ.get("SEED_ADMIN_PASSWORD")
    if not (name and admin_email and admin_pass):
        # No tenant vars -> only reference data was seeded. Fall back to
        # the first existing tenant for synonym seeding, if any.
        t = session.scalars(select(Tenant).limit(1)).first()
        return t.id if t else None

    t = session.scalars(select(Tenant).where(Tenant.legal_name == name)).first()
    if t is None:
        t = Tenant(legal_name=name, document_label="Invoice")
        session.add(t)
        session.flush()

    u = session.scalars(select(User).where(User.email == admin_email)).first()
    if u is None:
        session.add(
            User(
                tenant_id=t.id,
                email=admin_email,
                password_hash=argon2.hash(admin_pass),
                role=UserRole.owner,
            )
        )
    return t.id


def main() -> None:
    with SessionLocal() as session:
        hsn_added = seed_hsn(session)
        tenant_id = seed_tenant_and_admin(session)
        syn_added = seed_synonyms(session, tenant_id) if tenant_id else 0
        session.commit()

    print(
        f"seed: hsn_codes +{hsn_added}, synonyms +{syn_added}, "
        f"tenant={'yes' if tenant_id else 'none'}"
    )


if __name__ == "__main__":
    main()

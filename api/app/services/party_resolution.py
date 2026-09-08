"""Resolve a free-text party name (+ optional GSTIN / phone) to an existing
`party`. The gate behind `create_party` / `update_party` / the Tally import
commit, and the `/api/parties/resolve` type-ahead.

Ladder — stop at the first hit:

  1. gstin   exact on party.gstin (normalised upper)           method="gstin"  1.00
  2. phone   exact on digits(party.phone)                      method="phone"  0.97
  3. exact   normalize_name(name) == party.legal_name_normalized   "exact"     1.00
  4. fuzzy   pg_trgm similarity(legal_name_normalized, key)        "fuzzy"   <score>
             accept the top row only if >= _FUZZY_ACCEPT AND it beats the
             runner-up by >= _RUNNER_UP_GAP; otherwise weak=True and the
             candidates come back for the caller / UI to disambiguate.

Postgres only for step 4. SQLite (tests): steps 1-3 work, step 4 is skipped
and a non-matching query returns an empty `PartyMatch`.

`gstin` / `phone` are near-unique identifiers, so they win over any name
spelling. `exact` and the two hard keys are un-overridable by the caller;
only a `fuzzy` / weak result is bypassable with `force=True` at the router.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Party, PartyAddress
from app.models._mixins import PartyStatus
from app.services.parties import phone_digits_col

# Party names run longer and wordier than item names, and `similarity()` (whole
# string) scores lower than the `word_similarity()` items use. 0.45 keeps
# "steel tradrs" ~ "steel traders" (~0.75) and "steel traders" ~ "steel
# traders co" while rejecting unrelated firms.
_FUZZY_FLOOR = 0.45
# Auto "this IS the same party" — same bar as item_resolution.
_FUZZY_ACCEPT = 0.72
# Ambiguity guard — the top must clear the runner-up by this much.
_RUNNER_UP_GAP = 0.10
_CANDIDATE_N = 5

ResolveMethod = Literal["gstin", "phone", "exact", "fuzzy"]


@dataclass
class PartyCandidate:
    party_id: str
    legal_name: str
    gstin: str | None
    phone: str | None
    city: str | None
    last_txn_at: datetime | None
    status: str
    score: float


@dataclass
class PartyMatch:
    party_id: str | None
    method: ResolveMethod | None
    confidence: float | None
    candidates: list[PartyCandidate] = field(default_factory=list)
    # step 4 ran and found rows, but none was confident / unambiguous enough
    weak: bool = False


def _is_postgres(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _reason(match: PartyMatch, name: str) -> str:
    """Human-readable 409 `message` for the router."""
    if match.method == "gstin":
        return "A party with this GSTIN already exists"
    if match.method == "phone":
        return "A party with this phone number already exists"
    if match.method == "exact":
        return f"A party named '{name.strip()}' already exists"
    return "Similar parties already exist — pick one or confirm new"


def _city_of(session: Session, party_ids: list[str]) -> dict[str, str | None]:
    """Default-address city for each party id (best-effort recognition cue)."""
    if not party_ids:
        return {}
    rows = session.execute(
        select(PartyAddress.party_id, PartyAddress.city, PartyAddress.is_default)
        .where(PartyAddress.party_id.in_(party_ids))
        .order_by(PartyAddress.is_default.desc())
    ).all()
    out: dict[str, str | None] = {}
    for pid, city, _is_default in rows:
        out.setdefault(pid, city)  # first row per party wins (default sorts first)
    return out


def resolve_party(
    session: Session,
    tenant_id: str,
    name: str,
    *,
    gstin: str | None = None,
    phone: str | None = None,
    exclude_id: str | None = None,
    synonyms: dict[str, str] | None = None,
) -> PartyMatch:
    if synonyms is None:
        synonyms = load_synonym_map(session, tenant_id)
    key = normalize_name(name, synonyms)

    base = [Party.tenant_id == tenant_id, Party.status != PartyStatus.archived]
    if exclude_id:
        base.append(Party.id != exclude_id)

    # --- step 1: GSTIN (near-unique identity — beats any name spelling) ---
    g = (gstin or "").strip().upper()
    if g:
        hit = session.scalar(
            select(Party).where(*base, func.upper(Party.gstin) == g)
        )
        if hit is not None:
            return PartyMatch(hit.id, "gstin", 1.0)

    # --- step 2: phone (digits-only exact) ---
    pd = _digits(phone)
    if pd:
        hit = session.scalar(
            select(Party).where(*base, phone_digits_col() == pd)
        )
        if hit is not None:
            return PartyMatch(hit.id, "phone", 0.97)

    if not key:
        # Nothing to match a name against; the hard keys already missed.
        return PartyMatch(None, None, None)

    # --- step 3: exact normalized-name key ---
    exact = session.scalar(
        select(Party).where(*base, Party.legal_name_normalized == key)
    )
    if exact is not None:
        return PartyMatch(exact.id, "exact", 1.0)

    # --- step 4: trigram fuzzy (Postgres only) ---
    if not _is_postgres(session):
        return PartyMatch(None, None, None)

    sim = func.similarity(Party.legal_name_normalized, key)
    rows = session.execute(
        select(
            Party.id,
            Party.legal_name,
            Party.gstin,
            Party.phone,
            Party.last_txn_at,
            Party.status,
            sim.label("s"),
        )
        .where(*base, sim >= _FUZZY_FLOOR)
        .order_by(sim.desc())
        .limit(8)
    ).all()
    if not rows:
        return PartyMatch(None, None, None)

    cities = _city_of(session, [r.id for r in rows])
    cands = [
        PartyCandidate(
            party_id=r.id,
            legal_name=r.legal_name,
            gstin=r.gstin,
            phone=r.phone,
            city=cities.get(r.id),
            last_txn_at=r.last_txn_at,
            status=str(getattr(r.status, "value", r.status)),
            score=round(float(r.s), 3),
        )
        for r in rows
    ]
    cands.sort(key=lambda c: c.score, reverse=True)

    top = cands[0]
    runner_up = cands[1].score if len(cands) > 1 else 0.0
    if top.score >= _FUZZY_ACCEPT and (top.score - runner_up) >= _RUNNER_UP_GAP:
        return PartyMatch(
            top.party_id,
            "fuzzy",
            round(min(top.score, 0.99), 3),
            candidates=cands[:_CANDIDATE_N],
        )

    return PartyMatch(
        None, None, None, candidates=cands[:_CANDIDATE_N], weak=True
    )

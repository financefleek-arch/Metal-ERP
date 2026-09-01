"""Match a parsed Tally ledger against the tenant's existing parties.

Ladder (stop at the first hit):
  1. GSTIN exact  -> link + fill blanks
  2. PAN exact    -> link + fill blanks   (only when the ledger has no GSTIN)
  3. name trigram >= 0.82 to a single party -> FLAG for review
  4. no match     -> create new

Validation flags (any of these excludes the row from commit until fixed):
  bad_gstin, bad_pan, name_too_long, name_bad_chars, dual_lineage,
  duplicate_gstin_in_file, name_near_match
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Party
from app.models._mixins import PartyRole, PartyStatus
from app.reference import (
    LEGAL_NAME_MAX,
    validate_gstin,
    validate_legal_name,
    validate_pan,
)
from tools.tally_import.parser import TallyLedger

_NAME_SIM_FLOOR = 0.82


@dataclass
class MatchResult:
    method: str  # exact_gstin | exact_pan | name_fuzzy | none
    party_id: str | None = None
    proposed_role: PartyRole = PartyRole.customer
    flags: list[dict] = field(default_factory=list)


def _flag(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _norm_name(s: str) -> str:
    return " ".join(s.lower().split())


def match_ledger(
    session: Session,
    tenant_id: str,
    led: TallyLedger,
    role: PartyRole,
    *,
    gstins_in_file: dict[str, int],
    dual_lineage: bool,
) -> MatchResult:
    flags: list[dict] = []

    # --- name validity ---
    name = (led.name or "").strip()
    if len(name) > LEGAL_NAME_MAX:
        flags.append(
            _flag("name_too_long", f"Name is {len(name)} chars (max {LEGAL_NAME_MAX})")
        )
    else:
        try:
            validate_legal_name(name)
        except ValueError as e:
            flags.append(_flag("name_bad_chars", str(e)))

    # --- gstin / pan validity ---
    gstin_ok: str | None = None
    if led.gstin:
        try:
            gstin_ok = validate_gstin(led.gstin)
        except ValueError as e:
            flags.append(_flag("bad_gstin", str(e)))
    pan_ok: str | None = None
    if led.pan:
        try:
            pan_ok = validate_pan(led.pan)
        except ValueError as e:
            flags.append(_flag("bad_pan", str(e)))

    if gstin_ok and gstins_in_file.get(gstin_ok, 0) > 1:
        flags.append(
            _flag(
                "duplicate_gstin_in_file",
                "This GSTIN appears on more than one ledger in the file",
            )
        )

    if dual_lineage:
        flags.append(
            _flag(
                "dual_lineage",
                "Ledger sits under both Sundry Debtors and Sundry Creditors",
            )
        )

    # --- the ladder ---
    if gstin_ok:
        hit = session.scalar(
            select(Party).where(
                Party.tenant_id == tenant_id,
                Party.status != PartyStatus.archived,
                func.upper(Party.gstin) == gstin_ok,
            )
        )
        if hit:
            return MatchResult("exact_gstin", hit.id, role, flags)

    if not gstin_ok and pan_ok:
        hit = session.scalar(
            select(Party).where(
                Party.tenant_id == tenant_id,
                Party.status != PartyStatus.archived,
                func.upper(Party.pan) == pan_ok,
            )
        )
        if hit:
            return MatchResult("exact_pan", hit.id, role, flags)

    # name trigram — Postgres only; on SQLite fall back to exact-normalised.
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
    if is_pg:
        row = session.execute(
            select(Party.id, func.similarity(Party.legal_name, name).label("sim"))
            .where(
                Party.tenant_id == tenant_id,
                Party.status != PartyStatus.archived,
                func.similarity(Party.legal_name, name) >= _NAME_SIM_FLOOR,
            )
            .order_by(func.similarity(Party.legal_name, name).desc())
            .limit(2)
        ).all()
        if len(row) == 1:
            flags.append(
                _flag(
                    "name_near_match",
                    f"Looks like an existing party ({row[0].sim:.0%})",
                )
            )
            return MatchResult("name_fuzzy", row[0].id, role, flags)
    else:
        hit = session.scalar(
            select(Party).where(
                Party.tenant_id == tenant_id,
                Party.status != PartyStatus.archived,
                func.lower(Party.legal_name) == _norm_name(name),
            )
        )
        if hit:
            flags.append(_flag("name_near_match", "Same name as an existing party"))
            return MatchResult("name_fuzzy", hit.id, role, flags)

    return MatchResult("none", None, role, flags)

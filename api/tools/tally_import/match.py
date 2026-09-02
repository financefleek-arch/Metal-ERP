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

from collections.abc import Sequence
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

# A bulk import prefetches every party once and matches in memory rather than
# issuing up to three queries per ledger. Above this many staged ledgers the
# trigram near-match query is also skipped (exact GSTIN/PAN/name only) so the
# upload stays under a gateway timeout; the reviewer can still edit afterwards.
_BULK_TRIGRAM_CEILING = 800


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


# --------------------------------------------------------------------------
# bulk matcher (whole file in one pass)
# --------------------------------------------------------------------------


@dataclass
class _PartySnap:
    """The party id, keyed by whichever identity matched."""

    id: str


def _row_validity_flags(
    led: TallyLedger, *, gstins_in_file: dict[str, int], dual_lineage: bool
) -> tuple[list[dict], str | None, str | None]:
    """The per-ledger validation flags plus the normalised GSTIN / PAN.

    Identical checks to :func:`match_ledger`, factored out so the bulk path
    and the single-row path stay in lock-step.
    """
    flags: list[dict] = []

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

    return flags, gstin_ok, pan_ok


def match_ledgers_bulk(
    session: Session,
    tenant_id: str,
    ledgers: Sequence[tuple[TallyLedger, PartyRole, bool]],
    *,
    gstins_in_file: dict[str, int],
) -> list[MatchResult]:
    """Match a whole masters file at once.

    One prefetch of every non-archived party (indexed by upper-GSTIN,
    upper-PAN and normalised name) replaces up to three queries per ledger.
    The trigram near-match is only run, per still-unmatched ledger, while the
    file is small (see ``_BULK_TRIGRAM_CEILING``). Results are returned in the
    same order as ``ledgers``; each tuple is ``(ledger, role, dual_lineage)``.
    """
    rows = session.execute(
        select(Party.id, Party.gstin, Party.pan, Party.legal_name, Party.status).where(
            Party.tenant_id == tenant_id
        )
    ).all()

    by_gstin: dict[str, _PartySnap] = {}
    by_pan: dict[str, _PartySnap] = {}
    by_name: dict[str, _PartySnap] = {}
    for r in rows:
        if r.status == PartyStatus.archived:
            continue
        snap = _PartySnap(r.id)
        if r.gstin:
            by_gstin.setdefault(r.gstin.strip().upper(), snap)
        if r.pan:
            by_pan.setdefault(r.pan.strip().upper(), snap)
        if r.legal_name:
            by_name.setdefault(_norm_name(r.legal_name), snap)

    run_trigram = (
        len(ledgers) <= _BULK_TRIGRAM_CEILING
        and session.bind is not None
        and session.bind.dialect.name == "postgresql"
    )

    results: list[MatchResult] = []
    for led, role, dual in ledgers:
        flags, gstin_ok, pan_ok = _row_validity_flags(
            led, gstins_in_file=gstins_in_file, dual_lineage=dual
        )
        name = (led.name or "").strip()

        # --- 1: GSTIN ---
        if gstin_ok and gstin_ok.upper() in by_gstin:
            results.append(
                MatchResult("exact_gstin", by_gstin[gstin_ok.upper()].id, role, flags)
            )
            continue

        # --- 2: PAN (only when the ledger has no GSTIN) ---
        if not gstin_ok and pan_ok and pan_ok.upper() in by_pan:
            results.append(
                MatchResult("exact_pan", by_pan[pan_ok.upper()].id, role, flags)
            )
            continue

        # --- 3: exact normalised name ---
        key = _norm_name(name)
        if key and key in by_name:
            flags.append(_flag("name_near_match", "Same name as an existing party"))
            results.append(MatchResult("name_fuzzy", by_name[key].id, role, flags))
            continue

        # --- 3b: trigram near-match (small files, Postgres only) ---
        if key and run_trigram:
            near = session.execute(
                select(Party.id, func.similarity(Party.legal_name, name).label("sim"))
                .where(
                    Party.tenant_id == tenant_id,
                    Party.status != PartyStatus.archived,
                    func.similarity(Party.legal_name, name) >= _NAME_SIM_FLOOR,
                )
                .order_by(func.similarity(Party.legal_name, name).desc())
                .limit(2)
            ).all()
            if len(near) == 1:
                flags.append(
                    _flag(
                        "name_near_match",
                        f"Looks like an existing party ({near[0].sim:.0%})",
                    )
                )
                results.append(MatchResult("name_fuzzy", near[0].id, role, flags))
                continue

        # --- 4: none ---
        results.append(MatchResult("none", None, role, flags))

    return results

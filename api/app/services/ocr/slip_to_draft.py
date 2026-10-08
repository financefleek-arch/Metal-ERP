"""`SlipExtraction` -> a draft-ready set of invoice lines + party guess.

Resolves each extracted line against the item master and the extracted
customer name against the party master, reusing the exact same ladders the
typed-entry editor relies on (`item_resolution.resolve_item`,
`party_resolution.resolve_party`) — no new matching logic, so quality here
tracks whatever F12/party-dedupe already deliver.

A line that doesn't resolve confidently is NOT silently guessed: it is
returned with `item_id=None` and `needs_review=True` so the editor can flag
it for the operator, same as inward's "first pass always lands in review."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map
from app.domain.units import normalize_uom
from app.services.item_resolution import resolve_item
from app.services.ocr.extract_slip import ExtractedLine, SlipExtraction
from app.services.party_resolution import resolve_party


@dataclass
class DraftLine:
    item_id: str | None
    description: str
    quantity: Decimal
    uom: str | None
    unit_rate: Decimal
    needs_review: bool
    review_reason: str | None = None


@dataclass
class DraftFromSlip:
    party_id: str | None
    party_guess_name: str | None
    party_needs_review: bool
    lines: list[DraftLine] = field(default_factory=list)
    notes: str | None = None


def _resolve_line(session: Session, tenant_id: str, ln: ExtractedLine, synonyms: dict) -> DraftLine:
    if ln.quantity is None:
        return DraftLine(
            item_id=None,
            description=ln.description,
            quantity=Decimal("0"),
            uom=normalize_uom(ln.uom) or None,
            unit_rate=ln.rate or Decimal("0"),
            needs_review=True,
            review_reason="quantity not read from the slip",
        )

    match = resolve_item(session, tenant_id, ln.description, synonyms=synonyms)
    needs_review = match.item_id is None or match.weak
    reason = None
    if match.item_id is None:
        reason = "no confident item match — check the item"
    elif match.weak:
        reason = "ambiguous item match — please confirm"
    elif ln.rate is None:
        # Not a block on its own (pre-fill from last-sold-rate happens in the
        # router), but still worth a glance.
        reason = "rate not read from the slip"
        needs_review = True

    return DraftLine(
        item_id=match.item_id,
        description=ln.description,
        quantity=ln.quantity,
        uom=normalize_uom(ln.uom) or None,
        unit_rate=ln.rate or Decimal("0"),
        needs_review=needs_review,
        review_reason=reason,
    )


def build_draft(session: Session, tenant_id: str, extraction: SlipExtraction) -> DraftFromSlip:
    synonyms = load_synonym_map(session, tenant_id)

    party_id: str | None = None
    party_needs_review = False
    if extraction.party_name:
        pm = resolve_party(session, tenant_id, extraction.party_name, synonyms=synonyms)
        party_id = pm.party_id
        party_needs_review = pm.party_id is None or pm.weak

    lines = [_resolve_line(session, tenant_id, ln, synonyms) for ln in extraction.lines]

    return DraftFromSlip(
        party_id=party_id,
        party_guess_name=extraction.party_name,
        party_needs_review=party_needs_review,
        lines=lines,
        notes=extraction.notes,
    )

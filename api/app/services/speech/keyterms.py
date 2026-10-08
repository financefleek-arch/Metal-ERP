"""Deepgram keyterm-prompting source: distinctive words drawn from this
tenant's own most-billed item names, biasing the acoustic model toward the
shop's real vocabulary (fixes mishears like "wine glass" -> "vine gloves" on
terms it has no domain context for).

Real item names in this catalogue are full packaging descriptions ("1000 ML
True Colour Casserole Col Box 12 PC CTN (NO-000638)"), not short trade terms
— passing ~100 of those *whole* blew straight past Nova-3's 500-token
keyterm budget and Deepgram 400'd the entire request (hard failure, not a
silent truncation). This extracts single distinctive words instead: a
keyterm like "Casserole" helps pronunciation disambiguation as much as the
full SKU string would, at a fraction of the token cost, and lets far more
of the catalogue's actual vocabulary fit under budget.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item
from app.models._mixins import ItemStatus

_TOP_ITEMS = 300  # item rows to draw words from, not the final keyterm count
# Nova-3's keyterm budget is 500 TOKENS total per request (Deepgram docs),
# not 500 terms — stay well under it since a token often splits a word.
MAX_KEYTERMS = 150

# Packaging/measurement/SKU boilerplate that's common across *every* item in
# a catalogue like this — these add no pronunciation-disambiguation value
# and would otherwise crowd out the words that actually matter (the real
# product noun/brand). Matched case-insensitively against a whole token.
_NOISE_WORDS = {
    "pc", "pcs", "pc.", "ctn", "col", "box", "set", "in", "with", "lid",
    "no", "inch", "ml", "ltr", "lt", "cl", "bx", "mm", "on", "stand", "ltr.",
}
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{2,}")  # drop digits, codes, punctuation-only tokens


def top_item_keyterms(session: Session, tenant_id: str) -> list[str]:
    names = session.scalars(
        select(Item.name)
        .where(Item.tenant_id == tenant_id, Item.status != ItemStatus.archived)
        .order_by(Item.times_billed.desc())
        .limit(_TOP_ITEMS)
    ).all()

    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        for word in _WORD_RE.findall(name):
            key = word.lower()
            if key in _NOISE_WORDS or key in seen:
                continue
            seen.add(key)
            out.append(word)
            if len(out) >= MAX_KEYTERMS:
                return out
    return out

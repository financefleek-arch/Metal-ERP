"""Transcript -> {item query, quantity, uom, rate} — deterministic, no LLM.

Per the design decision in speech-invoice-capture-backlog: qty/unit/rate
extraction is a closed, numbers-and-unit-words vocabulary and should stay
rule-based (cheap, fast, no hallucination risk on money fields). An LLM
re-ranker is only useful for item-match disambiguation (not built in this
pilot pass — `resolve_item`'s own weak/no-match signal is surfaced to the
operator instead, same posture as the slip-OCR pilot).

Recognised shape, in roughly any order within the line:
  <item words> <quantity> <unit-word> [<rate> rupees|rate|ka]
e.g. "Monin syrup do dozen sau rupaye" -> qty=24? no: qty=2 (word "do"),
     uom="doz", rate=100, item_query="Monin syrup"

This is intentionally a thin pilot parser: it recognises digits and a small
Hindi/Hinglish number-word table, not full natural-language quantity phrases
("teen dozen" count-multiplied, etc. is NOT attempted here — a miss leaves
quantity=None and the line is flagged for review, same as the slip-OCR path
treats an unread quantity).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.domain.units import normalize_uom

_NUMBER_WORDS = {
    "ek": 1, "one": 1,
    "do": 2, "two": 2,
    "teen": 3, "three": 3,
    "char": 4, "chaar": 4, "four": 4,
    "panch": 5, "paanch": 5, "five": 5,
    "chhe": 6, "che": 6, "six": 6,
    "saat": 7, "sat": 7, "seven": 7,
    "aath": 8, "aaath": 8, "eight": 8,
    "nau": 9, "nine": 9,
    "das": 10, "dus": 10, "ten": 10,
    "gyarah": 11, "eleven": 11,
    "barah": 12, "bara": 12, "twelve": 12,
    "bees": 20, "twenty": 20,
    "pachaas": 50, "pachas": 50, "fifty": 50,
    "sau": 100, "hundred": 100,
}

# Unit words/aliases Deepgram's transcript is likely to spell out — kept in
# sync with units.json by normalize_uom(); this is just the recognition
# vocabulary for *finding* the token in free text (normalize_uom does the
# canonicalisation once found).
_UNIT_WORDS = (
    "dozen|doz|dz|kg|kilo|kilos|kilogram|nos|no|pc|pcs|piece|pieces|each|unit|units|"
    "gross|grs|gram|grams|gm|gms|quintal|qtl|ton|tons|tonne|tonnes|set|sets|pair|pairs|"
    "bundle|bdl|coil|coils|sheet|sheets|ft|feet|foot|m|meter|meters|metre|metres|"
    "sqft|sqm|ltr|litre|litres|liter|liters|box|boxes|pkt|packet|packets|btl|bottle|bottles"
)

_QTY_UNIT_RE = re.compile(
    rf"\b(\d+(?:\.\d+)?|{'|'.join(_NUMBER_WORDS)})\s+({_UNIT_WORDS})\b",
    re.IGNORECASE,
)
_RATE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*(?:rupa[iy]e[s]?|rs\.?|ka|ke|rate)\b",
    re.IGNORECASE,
)


def _num(token: str) -> Decimal:
    if re.fullmatch(r"\d+(\.\d+)?", token):
        return Decimal(token)
    return Decimal(_NUMBER_WORDS[token.lower()])


@dataclass
class ParsedVoiceLine:
    item_query: str
    quantity: Decimal | None
    uom: str | None
    rate: Decimal | None


def parse_voice_line(transcript: str) -> ParsedVoiceLine:
    text = transcript.strip()
    quantity: Decimal | None = None
    uom: str | None = None
    rate: Decimal | None = None

    qm = _QTY_UNIT_RE.search(text)
    if qm:
        quantity = _num(qm.group(1))
        uom = normalize_uom(qm.group(2))
        text = text[: qm.start()] + text[qm.end() :]

    rm = _RATE_RE.search(text)
    if rm:
        rate = _num(rm.group(1))
        text = text[: rm.start()] + text[rm.end() :]

    item_query = re.sub(r"\s+", " ", text).strip(" ,.-")
    return ParsedVoiceLine(item_query=item_query, quantity=quantity, uom=uom, rate=rate)

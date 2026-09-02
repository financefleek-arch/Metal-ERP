"""Rules-first item classifier — name (+ HSN, UOM) -> department, group, brand.

Pure. No I/O. Mirrors `domain.product_parse`: the caller loads the tenant's
synonym map and any learned rules and passes them in.

Pipeline, first hit wins:
  1. normalise   name -> lower, punctuation stripped, synonyms applied
  2. brand scan  longest brand phrase first -> `brand`
  3. learned     tenant-taught (phrase -> department/group) rows, longest first
  4. seed rules  the fixed keyword table (item_taxonomy.RULES)
  5. HSN fallback  2-digit chapter -> department, its "(unsorted)" group
  6. none        -> Other / Uncategorised, confidence 0.0

`confidence` gates the item's *status*, it is not a review queue:
  >= 0.70  assign, status = confirmed
  0.30-0.70  assign best guess, status = unconfirmed
  < 0.30   Other, status = unconfirmed
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.item_taxonomy import (
    BRANDS,
    HSN_CHAPTER_DEPARTMENT,
    HSN_FALLBACK_GROUP,
    OTHER_DEPARTMENT,
    RULES,
)
from app.domain.normalize import normalize_name

_CONF_RULE = 0.85          # a seed/learned keyword hit
_CONF_RULE_BRANDED = 0.9   # keyword hit AND a brand recognised
_CONF_HSN = 0.4            # only the HSN chapter placed it
_CONF_NONE = 0.0

CONFIDENCE_CONFIRM = 0.70   # >= -> status confirmed
CONFIDENCE_ASSIGN = 0.30    # >= -> assign best guess, else Other


@dataclass
class LearnedRule:
    """A tenant-taught mapping. `phrase` is already normalised."""

    phrase: str
    department: str
    group: str


@dataclass
class ClassifyResult:
    department: str
    group: str
    brand: str | None
    confidence: float
    rule_hit: str | None          # the phrase / signal that decided it
    source: str                   # "learned" | "rule" | "hsn" | "none"
    normalized: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_other(self) -> bool:
        return self.department == OTHER_DEPARTMENT

    @property
    def confirmed(self) -> bool:
        return self.confidence >= CONFIDENCE_CONFIRM

    @property
    def assignable(self) -> bool:
        return self.confidence >= CONFIDENCE_ASSIGN


def _match_brand(padded: str) -> tuple[str | None, str | None]:
    """(canonical brand, phrase that matched) or (None, None). Longest phrase
    across all brands first, so 'inox hydra' beats a bare 'hydra' only if both
    are present — here we simply prefer the longer phrase."""
    best: tuple[str, str] | None = None
    for brand, phrases in BRANDS.items():
        for ph in phrases:
            if ph in padded and (best is None or len(ph) > len(best[1])):
                best = (brand, ph)
    return best if best else (None, None)


def _hsn_chapter(hsn: str | None) -> str | None:
    if not hsn:
        return None
    digits = "".join(c for c in hsn if c.isdigit())
    return digits[:2] if len(digits) >= 2 else None


def classify_item(
    name: str,
    *,
    hsn: str | None = None,
    uom: str | None = None,
    synonyms: dict[str, str] | None = None,
    learned: list[LearnedRule] | None = None,
) -> ClassifyResult:
    norm = normalize_name(name, synonyms or {})
    padded = f" {norm} "

    brand, _brand_phrase = _match_brand(padded)

    if not norm:
        return ClassifyResult(
            OTHER_DEPARTMENT, HSN_FALLBACK_GROUP[OTHER_DEPARTMENT], brand,
            _CONF_NONE, None, "none", normalized=norm,
            notes=["name normalises to nothing"],
        )

    # --- learned rules first, longest phrase wins (most specific) ---
    for lr in sorted(learned or [], key=lambda r: len(r.phrase), reverse=True):
        ph = lr.phrase if lr.phrase.startswith(" ") else f" {lr.phrase} "
        if ph.strip() and ph in padded:
            return ClassifyResult(
                lr.department, lr.group, brand,
                _CONF_RULE_BRANDED if brand else _CONF_RULE,
                lr.phrase.strip(), "learned", normalized=norm,
            )

    # --- seed keyword table, first match wins (order encodes precedence) ---
    for dept, grp, phrases in RULES:
        for ph in phrases:
            if ph in padded:
                return ClassifyResult(
                    dept, grp, brand,
                    _CONF_RULE_BRANDED if brand else _CONF_RULE,
                    ph.strip(), "rule", normalized=norm,
                )

    # --- HSN chapter fallback ---
    chapter = _hsn_chapter(hsn)
    fallback_dept = HSN_CHAPTER_DEPARTMENT.get(chapter or "")
    if fallback_dept:
        return ClassifyResult(
            fallback_dept, HSN_FALLBACK_GROUP[fallback_dept], brand,
            _CONF_HSN, f"hsn:{chapter}", "hsn", normalized=norm,
            notes=["placed by HSN chapter only"],
        )

    # --- nothing ---
    return ClassifyResult(
        OTHER_DEPARTMENT, HSN_FALLBACK_GROUP[OTHER_DEPARTMENT], brand,
        _CONF_NONE, None, "none", normalized=norm,
    )

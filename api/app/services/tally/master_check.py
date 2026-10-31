"""Is this supplier or item already in the company's Tally? (S2.7)

`ACTION="Create"` on a master Tally already has ALTERS it, and a near-duplicate name makes a second
ledger the accountant has to merge. So before a purchase push asks Tally to create a supplier or an
item, its name is compared with what the company is known to hold (the last masters pull or stock
check): an exact match is left alone, a near match stops the push for the user to decide.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.models import TallyCompany

NEAR = 0.88  # similarity at or above which two different names are probably the same master


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def known_supplier_names(company: TallyCompany) -> dict[str, str]:
    """normalized name -> Tally's spelling, for the ledgers last seen in this company."""
    out: dict[str, str] = {}
    for led in company.known_ledgers or []:
        if led.get("kind", "ledger") == "ledger" and led.get("name"):
            out[_norm(led["name"])] = str(led["name"])
    return out


def known_item_names(company: TallyCompany) -> dict[str, str]:
    """normalized name -> the same name (the stock check keeps normalized names only)."""
    return {n: n for n in (company.known_stock_items or []) if n}


def classify(name: str, known: dict[str, str]) -> tuple[str, str | None]:
    """('exact' | 'near' | 'new', Tally's spelling of the match)."""
    key = _norm(name)
    if not key or not known:
        return "new", None
    if key in known:
        return "exact", known[key]
    best, best_score = None, 0.0
    for other in known:
        score = SequenceMatcher(None, key, other).ratio()
        if score > best_score:
            best, best_score = other, score
    if best is not None and best_score >= NEAR:
        return "near", known[best]
    return "new", None

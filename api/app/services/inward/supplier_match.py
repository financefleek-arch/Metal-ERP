"""Match a bill line to an item this supplier already offered us in a price list.

A price-list product is the supplier's own thing: it has the supplier's code and the supplier's
wording. When a bill from the same supplier names the same thing, we should link the item that
product became, not stage a duplicate. Two cheap, exact readings come before the general
matcher (they never guess):

  1. the supplier's own product code appears as a word in the line description;
  2. the line's normalised description equals the supplier's product name.

A hit is only used when exactly one item fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import normalize_name
from app.models import CatalogProduct

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/.]*[A-Za-z0-9]|[A-Za-z0-9]")
_MIN_CODE = 3  # shorter codes ("A1") show up in ordinary descriptions by accident


@dataclass
class SupplierIndex:
    by_code: dict[str, set[str]]
    by_name: dict[str, set[str]]

    def __bool__(self) -> bool:
        return bool(self.by_code or self.by_name)


def build_index(
    session: Session, tenant_id: str, supplier_party_id: str | None, synonyms: dict[str, str]
) -> SupplierIndex:
    idx = SupplierIndex({}, {})
    if not supplier_party_id:
        return idx
    for p in session.scalars(
        select(CatalogProduct).where(
            CatalogProduct.tenant_id == tenant_id,
            CatalogProduct.supplier_party_id == supplier_party_id,
            CatalogProduct.item_id.is_not(None),
        )
    ):
        assert p.item_id is not None
        code = p.supplier_code.strip().casefold()
        if len(code) >= _MIN_CODE:
            idx.by_code.setdefault(code, set()).add(p.item_id)
        name = normalize_name(p.display_name, synonyms)
        if name:
            idx.by_name.setdefault(name, set()).add(p.item_id)
    return idx


def match(
    idx: SupplierIndex, description: str, synonyms: dict[str, str]
) -> tuple[str, float] | None:
    """(item id, confidence) when this supplier's own code or name pins down one item."""
    if not idx:
        return None
    hits = {
        item for tok in _TOKEN.findall(description) for item in idx.by_code.get(tok.casefold(), ())
    }
    if len(hits) == 1:
        return next(iter(hits)), 0.99
    name = normalize_name(description, synonyms)
    named = idx.by_name.get(name, set()) if name else set()
    if len(named) == 1:
        return next(iter(named)), 1.0
    return None

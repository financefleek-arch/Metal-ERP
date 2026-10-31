"""The ledger names a voucher uses, per Tally company (S1.5).

One source: `tally_company.ledger_map`. Sales push, purchase push and the manual purchase export all read
the document's company through here, so they cannot disagree. A firm with no Tally company yet keeps its
names on the legacy `tally_ledger_config` row (inward settings), which is only read and written then.
"""

from __future__ import annotations

from app.models import TallyCompany
from app.services.inward.tally_xml import LedgerConfig

# LedgerConfigIO / LedgerConfig field -> key in `ledger_map`
MAP_KEYS = {
    "creditors_group": "creditors_parent",
    "purchase_ledger": "purchase_ledger",
    "cgst_ledger": "cgst_ledger",
    "sgst_ledger": "sgst_ledger",
    "igst_ledger": "igst_ledger",
    "round_off_ledger": "round_off_ledger",
    "xml_encoding": "xml_encoding",
}


def ledger_config(company: TallyCompany | None, *, xml_encoding: str | None = None) -> LedgerConfig:
    """The purchase-voucher ledger names for `company`; a name the map lacks takes the standard one."""
    ledger_map = (company.ledger_map if company is not None else None) or {}
    base = LedgerConfig()
    values = {
        field: (ledger_map.get(key) or getattr(base, field)) for field, key in MAP_KEYS.items()
    }
    if xml_encoding:
        values["xml_encoding"] = xml_encoding
    return LedgerConfig(**values)


def save_ledger_config(company: TallyCompany, values: dict[str, str]) -> None:
    """Write the names into the company's map, leaving every other key (sales ledger, ...) alone."""
    merged = dict(company.ledger_map or {})
    for field, key in MAP_KEYS.items():
        if field in values and values[field]:
            merged[key] = values[field]
    company.ledger_map = merged  # reassigned, so the JSON column notices the change

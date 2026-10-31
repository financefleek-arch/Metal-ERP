"""Who owns which field of a customer or item that a Tally pull touches (S1.9, decision D-8).

A pull may only FILL a blank of the fields listed under `*_FILLED_FROM_TALLY`, never change a value we
already hold. Everything in `*_OURS` is commercial data the shop maintains here and a pull must never
write. `tests/test_tally_ownership.py` reads the two importers' source and fails if one writes a field
that is on neither list, so a new field has to be put on a side on purpose.

A Tally-owned field whose value differs from ours is not overwritten; the commit reports it as a
conflict (`ImportConflict`) so the owner can decide.
"""

from __future__ import annotations

from pydantic import BaseModel

# fields a masters pull may fill when ours is blank
PARTY_FILLED_FROM_TALLY = frozenset(
    {"gstin", "pan", "phone", "email", "default_state_code", "role", "addresses"}
)
ITEM_FILLED_FROM_TALLY = frozenset({"uom", "hsn_code", "default_rate", "mrp", "gst_rate"})

# the subset where a differing Tally value is reported instead of silently ignored
PARTY_CONFLICT_FIELDS = ("gstin", "pan")
ITEM_CONFLICT_FIELDS = ("hsn_code", "gst_rate")

# commercial data a pull must never write
PARTY_OURS = frozenset(
    {
        "legal_name", "credit_days", "default_credit_days", "opening_balance", "whatsapp_consent",
        "link_token", "notes", "status", "default_company_id",
    }
)
ITEM_OURS = frozenset(
    {
        "name", "sale_rate", "last_purchase_rate", "availability", "pack_qty", "carton_qty",
        "sku", "barcode", "primary_media_id", "catalog_flags", "status",
    }
)


class ImportConflict(BaseModel):
    """A Tally value that differs from ours on a field Tally owns; we kept ours."""

    name: str
    field: str
    ours: str
    tally: str


def conflict(name: str, field: str, ours: object, theirs: object) -> ImportConflict | None:
    o, t = str(ours or "").strip(), str(theirs or "").strip()
    if o and t and o.upper() != t.upper():
        return ImportConflict(name=name, field=field, ours=o, tally=t)
    return None

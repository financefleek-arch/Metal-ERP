"""Supplier resolution — extracted supplier fields -> a party link or a staged
new party.

  1. GSTIN exact on party.gstin where role in (supplier, both) -> link.
     A matched `customer` is staged for promotion to `both` on approve.
  2. No GSTIN on the PDF -> normalized-name trigram against supplier parties
     (Postgres; reuses ix_party_legal_name_trgm). A single hit > 0.85 is
     *proposed*, never auto-linked.
  3. No match -> build new_supplier_staged_json (legal_name, gstin, pan from
     GSTIN chars 3-12, default_state_code from the prefix, role=supplier,
     one address). Written only on Approve.

`supply_type`: supplier prefix == buyer (tenant) prefix -> intra (CGST+SGST)
else inter (IGST).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Party
from app.models._mixins import PartyRole, PartyStatus, SupplyType
from app.reference import PAN_RE

_NAME_MATCH_FLOOR = 0.85


@dataclass
class SupplierResolution:
    matched_party_id: str | None = None
    matched_party_role: PartyRole | None = None
    promote_to_both: bool = False  # matched party is a plain customer
    proposed_party_id: str | None = None  # name-trigram hit, needs confirmation
    new_supplier_staged: dict[str, Any] | None = None
    supply_type: SupplyType | None = None
    place_of_supply_state_code: str | None = None
    method: str = "none"  # 'gstin' | 'name_proposed' | 'staged'
    notes: list[str] = field(default_factory=list)


def _pan_from_gstin(gstin: str | None) -> str | None:
    if not gstin or len(gstin) < 12:
        return None
    candidate = gstin[2:12]
    return candidate if PAN_RE.match(candidate) else None


def resolve_supplier(
    session: Session,
    tenant_id: str,
    *,
    supplier_name: str | None,
    supplier_gstin: str | None,
    buyer_gstin: str | None,
    place_of_supply_state_code: str | None,
    address_block: dict[str, Any] | None = None,
) -> SupplierResolution:
    res = SupplierResolution()

    supplier_prefix = supplier_gstin[:2] if supplier_gstin else None
    buyer_prefix = buyer_gstin[:2] if buyer_gstin else None
    res.place_of_supply_state_code = place_of_supply_state_code or supplier_prefix
    if supplier_prefix and buyer_prefix:
        res.supply_type = (
            SupplyType.intra if supplier_prefix == buyer_prefix else SupplyType.inter
        )

    # --- 1. GSTIN exact ---
    if supplier_gstin:
        hit = session.scalar(
            select(Party).where(
                Party.tenant_id == tenant_id,
                Party.gstin == supplier_gstin,
                Party.status != PartyStatus.archived,
            )
        )
        if hit is not None:
            res.matched_party_id = hit.id
            res.matched_party_role = hit.role
            res.method = "gstin"
            if hit.role == PartyRole.customer:
                res.promote_to_both = True
                res.notes.append("matched a customer party — will promote to 'both'")
            return res

    # --- 2. name trigram (Postgres only) ---
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"
    if not supplier_gstin and supplier_name and is_pg:
        sim = func.similarity(Party.legal_name, supplier_name)
        row = session.execute(
            select(Party.id, sim.label("s"))
            .where(
                Party.tenant_id == tenant_id,
                Party.status != PartyStatus.archived,
                or_(Party.role == PartyRole.supplier, Party.role == PartyRole.both),
                sim >= _NAME_MATCH_FLOOR,
            )
            .order_by(sim.desc())
            .limit(2)
        ).all()
        if len(row) == 1 or (len(row) == 2 and row[0].s - row[1].s >= 0.1):
            res.proposed_party_id = row[0].id
            res.method = "name_proposed"
            res.notes.append(
                f"proposed by name similarity {float(row[0].s):.2f} — confirm before linking"
            )
            return res

    # --- 3. stage new ---
    staged: dict[str, Any] = {
        "legal_name": supplier_name,
        "gstin": supplier_gstin,
        "pan": _pan_from_gstin(supplier_gstin),
        "default_state_code": supplier_prefix,
        "role": PartyRole.supplier.value,
        "status": PartyStatus.active.value,
    }
    if address_block:
        staged["address"] = {
            "type": "both",
            "line1": address_block.get("line1"),
            "line2": address_block.get("line2"),
            "city": address_block.get("city"),
            "state_code": address_block.get("state_code") or supplier_prefix,
            "pincode": address_block.get("pincode"),
            "is_default": True,
        }
    res.new_supplier_staged = staged
    res.method = "staged"
    res.notes.append("no match — a new supplier party will be created on approve")
    return res

"""Approve an inward bill — one transaction.

  1. re-validate: reconciled + every line resolved + supplier resolved (else 422)
  2. supplier provenance:
       - staged new party -> create with source=inward_bill, source_ref=<bill id>,
         role=supplier, status=active, + address
       - matched customer  -> promote role to 'both' (source/source_ref untouched)
       - either way: party.last_txn_at = bill_date (forward-only)
  3. create staged new items (source=auto_from_purchase, status=unconfirmed),
     normalized-key dedupe (a race that finds an existing name links instead)
  4. link every line's matched_item_id; bump last_purchase_rate / last_purchased_at
  5. build the Tally XML -> write to the volume -> set tally_xml_path
  6. status = approved
  7. audit_log
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AuditLog,
    InwardBill,
    Item,
    Party,
    PartyAddress,
    TallyLedgerConfig,
)
from app.models._mixins import (
    AddressType,
    InwardStatus,
    ItemSource,
    ItemType,
    MatchMethod,
    PartyRole,
    PartySource,
    PartyStatus,
)
from app.reference import (
    validate_address_line,
    validate_city,
    validate_phone,
    validate_pincode,
    validate_state_code,
)
from app.services.inward.tally_xml import LedgerConfig, build_xml_bytes


def _safe[T](validator: Callable[[str | None], T], value: object) -> T | None:
    """Run a reference validator; on a malformed extracted value, drop it
    rather than 422 the whole approval. `None`/blank pass straight through.
    """
    if value in (None, ""):
        return None
    try:
        return validator(str(value))
    except ValueError:
        return None


class ApproveError(Exception):
    """Raised when the approve gate fails — router maps to 422."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))




@dataclass
class ApproveResult:
    created_supplier_id: str | None
    promoted_party_id: str | None
    created_item_ids: list[str]
    linked_line_count: int
    xml_path: str


def approve_gate(bill: InwardBill) -> list[str]:
    reasons: list[str] = []
    if bill.status == InwardStatus.rejected:
        reasons.append("bill is rejected")
    if bill.status == InwardStatus.error:
        reasons.append("bill is in an error state — re-extract first")
    if not bill.reconciled:
        d = bill.reconcile_discrepancy
        reasons.append(f"totals do not reconcile (off by {d})")
    if bill.matched_party_id is None and not bill.new_supplier_staged_json:
        reasons.append("supplier is not resolved")
    for line in bill.lines:
        resolved = line.matched_item_id is not None or bool(line.new_item_staged_json)
        if not resolved:
            reasons.append(f"line {line.sl_no} has no item match")
    return reasons


def get_ledger_config(session: Session, tenant_id: str) -> TallyLedgerConfig:
    cfg = session.get(TallyLedgerConfig, tenant_id)
    if cfg is None:
        cfg = TallyLedgerConfig(tenant_id=tenant_id)
        session.add(cfg)
        session.flush()
    return cfg


def approve_bill(
    session: Session, bill: InwardBill, *, actor_user_id: str | None
) -> ApproveResult:
    reasons = approve_gate(bill)
    if reasons:
        raise ApproveError(reasons)

    if bill.status == InwardStatus.approved:
        raise ApproveError(["bill is already approved"])

    settings = get_settings()
    now = datetime.now(UTC)

    # --- 2. supplier ---
    created_supplier_id: str | None = None
    promoted_party_id: str | None = None
    party: Party | None = None

    if bill.matched_party_id:
        party = session.get(Party, bill.matched_party_id)
        if party is not None and party.role == PartyRole.customer:
            party.role = PartyRole.both
            promoted_party_id = party.id
    elif bill.new_supplier_staged_json:
        staged = bill.new_supplier_staged_json
        party = Party(
            tenant_id=bill.tenant_id,
            legal_name=staged.get("legal_name") or bill.supplier_name or "Unknown Supplier",
            gstin=staged.get("gstin"),
            pan=staged.get("pan"),
            phone=_safe(validate_phone, staged.get("phone")),
            default_state_code=staged.get("default_state_code"),
            role=PartyRole.supplier,
            status=PartyStatus.active,
            source=PartySource.inward_bill,
            source_ref=bill.id,
        )
        addr = staged.get("address")
        if addr and any(addr.get(k) for k in ("line1", "line2", "pincode")):
            party.addresses.append(
                PartyAddress(
                    type=AddressType.both,
                    line1=_safe(validate_address_line, addr.get("line1")),
                    line2=_safe(validate_address_line, addr.get("line2")),
                    city=_safe(validate_city, addr.get("city")),
                    state_code=_safe(validate_state_code, addr.get("state_code")),
                    pincode=_safe(validate_pincode, addr.get("pincode")),
                    is_default=True,
                )
            )
        session.add(party)
        session.flush()
        created_supplier_id = party.id
        bill.matched_party_id = party.id

    # forward-only last_txn_at
    if party is not None and bill.bill_date is not None:
        bill_dt = datetime(
            bill.bill_date.year, bill.bill_date.month, bill.bill_date.day, tzinfo=UTC
        )
        if party.last_txn_at is None or party.last_txn_at < bill_dt:
            party.last_txn_at = bill_dt

    # --- 3 + 4. items + line links ---
    from app.services.catalogue.classify_apply import Classifier

    classifier = Classifier(session, bill.tenant_id)
    created_item_ids: list[str] = []
    linked = 0
    for line in bill.lines:
        item_id = line.matched_item_id
        if item_id is None and line.new_item_staged_json:
            staged = line.new_item_staged_json
            norm = staged.get("name_normalized") or ""
            existing = session.scalar(
                select(Item).where(
                    Item.tenant_id == bill.tenant_id,
                    Item.name_normalized == norm,
                )
            )
            if existing is not None:
                item_id = existing.id
            else:
                item_name = staged.get("name") or line.description
                applied = classifier.apply(
                    item_name,
                    hsn=staged.get("hsn_code"),
                    uom=staged.get("uom"),
                )
                item = Item(
                    tenant_id=bill.tenant_id,
                    name=item_name,
                    name_normalized=norm,
                    item_type=ItemType(staged.get("item_type") or ItemType.bulk.value),
                    uom=staged.get("uom"),
                    hsn_code=staged.get("hsn_code"),
                    group_id=applied.group_id,
                    category_id=applied.category_id,
                    source=ItemSource.auto_from_purchase,
                    status=applied.status,
                )
                session.add(item)
                session.flush()
                item_id = item.id
                created_item_ids.append(item_id)
            line.matched_item_id = item_id
            if line.match_method != MatchMethod.manual:
                line.match_method = MatchMethod.new

        if item_id is not None:
            linked += 1
            linked_item = session.get(Item, item_id)
            if linked_item is not None and line.unit_rate is not None:
                # item.last_purchase_rate is Mapped[float | None] (M1 convention)
                linked_item.last_purchase_rate = float(line.unit_rate)
                linked_item.last_purchased_at = now

    # --- 5. XML ---
    cfg_row = get_ledger_config(session, bill.tenant_id)
    cfg = LedgerConfig(
        creditors_group=cfg_row.creditors_group,
        purchase_ledger=cfg_row.purchase_ledger,
        cgst_ledger=cfg_row.cgst_ledger,
        sgst_ledger=cfg_row.sgst_ledger,
        igst_ledger=cfg_row.igst_ledger,
        round_off_ledger=cfg_row.round_off_ledger,
        xml_encoding=cfg_row.xml_encoding,
    )
    new_item_names: set[str] = {
        name
        for line in bill.lines
        if (line.new_item_staged_json)
        and (name := (line.new_item_staged_json or {}).get("name"))
    }
    party_name = (
        party.legal_name
        if party is not None
        else (bill.supplier_name or "Unknown Supplier")
    )
    new_supplier_name = (
        party.legal_name if (created_supplier_id and party is not None) else None
    )

    xml_bytes = build_xml_bytes(
        bill,
        cfg,
        party_name=party_name,
        new_supplier_name=new_supplier_name,
        new_item_names=new_item_names,
    )
    out_dir = Path(settings.inward_dir) / "xml"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Keyed on bill.id (stable, collision-proof). A re-approve of the same
    # bill overwrites in place.
    xml_path = out_dir / f"inward-{bill.id}.xml"
    xml_path.write_bytes(xml_bytes)
    bill.tally_xml_path = str(xml_path)

    # The source PDF has served its purpose — the XML is the durable
    # artefact from here on. Drop the PDF to keep the volume small; the
    # user can always re-upload, which regenerates against a fresh bill.
    if bill.source_pdf_path:
        Path(bill.source_pdf_path).unlink(missing_ok=True)
        bill.source_pdf_path = None

    # --- 6 + 7 ---
    bill.status = InwardStatus.approved
    session.add(
        AuditLog(
            tenant_id=bill.tenant_id,
            actor_user_id=actor_user_id,
            entity="inward_bill",
            entity_id=bill.id,
            action="approve",
            after_json={
                "created_supplier_id": created_supplier_id,
                "promoted_party_id": promoted_party_id,
                "created_item_ids": created_item_ids,
                "linked_line_count": linked,
                "tally_xml_path": bill.tally_xml_path,
            },
        )
    )
    session.flush()

    return ApproveResult(
        created_supplier_id=created_supplier_id,
        promoted_party_id=promoted_party_id,
        created_item_ids=created_item_ids,
        linked_line_count=linked,
        xml_path=str(xml_path),
    )

"""Phase 1 smoke tests: the schema is coherent and the core relationships
persist and read back.
"""

from __future__ import annotations

from datetime import date

from app.models import (
    HsnCode,
    Invoice,
    InvoiceLine,
    Item,
    Party,
    PartyAddress,
    Tenant,
    User,
)
from app.models._mixins import InvoiceStatus, ItemStatus, ItemType, UserRole


def test_all_tables_registered() -> None:
    from app.db import Base

    expected = {
        "tenant",
        "app_user",
        "party",
        "party_address",
        "item",
        "item_alias",
        "synonym",
        "product_group",
        "hsn_code",
        "invoice",
        "invoice_line",
        "number_sequence",
        "audit_log",
        # ext_inward_import (migration 0004)
        "inward_bill",
        "inward_bill_line",
        "supplier_template",
        "tally_ledger_config",
        "extraction_run",
        "job",
    }
    assert expected <= set(Base.metadata.tables)


def test_tenant_user_party_item_invoice_roundtrip(session) -> None:  # type: ignore[no-untyped-def]
    t = Tenant(legal_name="Sethia Metal Store", document_label="Invoice")
    session.add(t)
    session.flush()

    u = User(
        tenant_id=t.id,
        email="owner@example.com",
        password_hash="x",
        role=UserRole.owner,
    )
    session.add(u)

    session.add(HsnCode(code="73239390", description="SS household articles", chapter="73"))

    p = Party(tenant_id=t.id, legal_name="Jay Matadee Enterprises")
    p.addresses.append(PartyAddress(line1="Millanpally", city="Darjeeling", state_code="19"))
    session.add(p)
    session.flush()

    it = Item(
        tenant_id=t.id,
        name="SS Utensil",
        name_normalized="ss utensil",
        item_type=ItemType.bulk,
        uom="Kgs",
        hsn_code="73239390",
        status=ItemStatus.confirmed,
    )
    session.add(it)
    session.flush()

    inv = Invoice(
        tenant_id=t.id,
        fy="2026-27",
        date=date(2026, 8, 29),
        party_id=p.id,
        status=InvoiceStatus.draft,
    )
    inv.lines.append(
        InvoiceLine(
            sl_no=1,
            item_id=it.id,
            description="SS Utensil",
            hsn_code="73239390",
            quantity=900,
            uom="Kgs",
            unit_rate=264,
        )
    )
    session.add(inv)
    session.flush()

    got = session.get(Invoice, inv.id)
    assert got is not None
    assert got.status is InvoiceStatus.draft
    assert got.number is None  # not assigned until finalize
    assert len(got.lines) == 1
    assert got.lines[0].description == "SS Utensil"
    assert got.lines[0].item.name_normalized == "ss utensil"
    assert got.party.legal_name == "Jay Matadee Enterprises"
    assert got.party.addresses[0].state_code == "19"


def test_dormant_gst_columns_default_safely(session) -> None:  # type: ignore[no-untyped-def]
    t = Tenant(legal_name="T")
    session.add(t)
    session.flush()
    assert t.gst_enabled is False

    p = Party(tenant_id=t.id, legal_name="P")
    session.add(p)
    session.flush()

    inv = Invoice(tenant_id=t.id, fy="2026-27", date=date(2026, 4, 1), party_id=p.id)
    session.add(inv)
    session.flush()

    assert inv.reverse_charge is False
    assert inv.template_version == "v1-nongst"
    assert inv.cgst_total is None
    assert inv.irn is None

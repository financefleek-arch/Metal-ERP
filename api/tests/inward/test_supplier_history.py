"""A bill from a supplier we hold a price list for: link their products, flag odd prices."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CatalogProduct, Item, Party, SupplierPricePoint
from app.models._mixins import PartyRole
from tests.inward.test_api_flow import _upload_sugal

GSTIN = "19BHBPK1450P1Z3"


def _tenant(client: TestClient, h: dict[str, str]) -> str:
    return str(client.get("/api/auth/me", headers=h).json()["tenant_id"])


def _setup(client, h, bill, *, quote: str | None):
    """The supplier, one item their price list made, and (optionally) its latest quote."""
    tid = _tenant(client, h)
    name = bill["lines"][0]["new_item_staged_json"]["name"]
    with SessionLocal() as s:
        sup = Party(tenant_id=tid, legal_name="Sugal Foods", gstin=GSTIN, role=PartyRole.supplier)
        s.add(sup)
        mine = Item(
            tenant_id=tid, name="Our name for it", name_normalized="our name for it", uom="nos"
        )
        s.add(mine)
        s.flush()
        prod = CatalogProduct(
            tenant_id=tid,
            supplier_party_id=sup.id,
            supplier_code="ZZ-900",
            code="100900",
            display_name=name,
            name_normalized="x",
            item_id=mine.id,
        )
        s.add(prod)
        s.flush()
        if quote is not None:
            s.add(
                SupplierPricePoint(
                    tenant_id=tid,
                    supplier_party_id=sup.id,
                    product_id=prod.id,
                    source="quote",
                    source_id="cat",
                    price=Decimal(quote),
                    pack_qty=1,
                    on_date=date(2025, 8, 1),
                )
            )
        s.commit()
        return sup.id, mine.id


def test_a_suppliers_own_product_is_linked_instead_of_a_duplicate(
    inward_client, sugal_pdf_bytes, seeded_hsn
) -> None:
    client, h = inward_client
    bill = _upload_sugal(client, h, sugal_pdf_bytes)
    _sup, mine = _setup(client, h, bill, quote=None)
    r = client.post(f"/api/inward-bills/{bill['id']}/re-extract", headers=h)
    assert r.status_code == 200, r.text
    first = r.json()["lines"][0]
    assert first["matched_item_id"] == mine and first["match_method"] == "code"
    assert first["note_flag"] is None  # no quote to compare with


def test_a_bill_above_the_quote_and_a_discontinued_item_are_flagged(
    inward_client, sugal_pdf_bytes, seeded_hsn
) -> None:
    client, h = inward_client
    bill = _upload_sugal(client, h, sugal_pdf_bytes)
    _sup, mine = _setup(client, h, bill, quote="1.00")
    gone = bill["lines"][1]["new_item_staged_json"]["name"]
    dead = client.post("/api/items", headers=h, json={"name": gone, "uom": "nos"}).json()
    client.patch(f"/api/items/{dead['id']}", headers=h, json={"availability": "discontinued"})
    lines = client.post(f"/api/inward-bills/{bill['id']}/re-extract", headers=h).json()["lines"]
    assert lines[0]["note_flag"] == "above_quote" and Decimal(lines[0]["quoted_rate"]) == 1
    assert lines[1]["matched_item_id"] == dead["id"] and lines[1]["note_flag"] == "discontinued"
    assert lines[2]["note_flag"] is None


def test_approving_records_what_was_paid_and_remembers_the_wording(
    inward_client, sugal_pdf_bytes, seeded_hsn
) -> None:
    client, h = inward_client
    bill = _upload_sugal(client, h, sugal_pdf_bytes)
    sup, mine = _setup(client, h, bill, quote=None)
    client.post(f"/api/inward-bills/{bill['id']}/re-extract", headers=h)
    ok = client.post(f"/api/inward-bills/{bill['id']}/approve", headers=h)
    assert ok.status_code == 200, ok.text
    with SessionLocal() as s:
        points = list(
            s.scalars(select(SupplierPricePoint).where(SupplierPricePoint.source == "bill"))
        )
        assert len(points) == 12 and all(p.supplier_party_id == sup for p in points)
        assert any(p.item_id == mine for p in points)


def test_a_supplier_page_lists_its_bills_and_the_spend(
    inward_client, sugal_pdf_bytes, seeded_hsn
) -> None:
    client, h = inward_client
    bill = _upload_sugal(client, h, sugal_pdf_bytes)
    sup, _mine = _setup(client, h, bill, quote=None)
    client.post(f"/api/inward-bills/{bill['id']}/re-extract", headers=h)
    before = client.get(f"/api/documents/by-supplier/{sup}", headers=h).json()
    assert [b["id"] for b in before["bills"]] == [bill["id"]] and Decimal(before["spend"]) == 0
    assert client.post(f"/api/inward-bills/{bill['id']}/approve", headers=h).status_code == 200
    after = client.get(f"/api/documents/by-supplier/{sup}", headers=h).json()
    assert Decimal(after["spend"]) == Decimal("42445.00")
    assert after["bills"][0]["status"] == "approved"

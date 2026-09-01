"""End-to-end API flow for the Sugal Foods PDF:

upload -> extract -> review (needs_review, reconciled green, 12 lines staged
NEW) -> approve -> supplier + items created -> Tally XML downloads and parses.

Runs on the conftest SQLite DB. With an empty catalogue every line resolves
as a NEW item, so fuzzy-only line matching is sufficient here.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Item, Party


def _upload_sugal(client: TestClient, headers: dict[str, str], pdf: bytes) -> dict:
    r = client.post(
        "/api/inward-bills",
        headers=headers,
        files={"files": ("sugal-foods-INV2526-5667.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body) == 1
    return body[0]


def test_upload_extracts_and_reconciles(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    bill = _upload_sugal(client, headers, sugal_pdf_bytes)

    assert bill["status"] == "needs_review"
    assert bill["extraction_method"] == "table"
    assert bill["bill_no"] == "INV2526-5667"
    assert bill["bill_date"] == "2025-08-25"

    recon = bill["reconciliation"]
    assert recon["reconciled"] is True
    assert Decimal(recon["grand_total"]) == Decimal("42445.00")
    assert Decimal(recon["taxable_total"]) == Decimal("35970.22")

    assert len(bill["lines"]) == 12
    # empty catalogue -> every line staged NEW
    for ln in bill["lines"]:
        assert ln["match_method"] == "new"
        assert ln["matched_item_id"] is None
        assert ln["new_item_staged_json"]["name"].startswith("Monin")
        assert ln["new_item_staged_json"]["hsn_code"] == "21069092"  # HSN is seeded
        assert ln["review_flag"] == "new"

    # supplier: no GSTIN match -> staged new
    sup = bill["supplier"]
    assert sup["matched_party_id"] is None
    assert sup["staged"]["gstin"] == "19BHBPK1450P1Z3"
    assert sup["staged"]["pan"] == "BHBPK1450P"  # GSTIN chars 3-12
    assert sup["staged"]["default_state_code"] == "19"
    assert sup["supply_type"] == "intra"  # 19 == 19

    assert bill["approve_blockers"] == []


def test_full_approve_creates_masters_and_xml(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    bill = _upload_sugal(client, headers, sugal_pdf_bytes)
    bill_id = bill["id"]

    r = client.post(f"/api/inward-bills/{bill_id}/approve", headers=headers)
    assert r.status_code == 200, r.text
    result = r.json()

    assert result["status"] == "approved"
    assert result["created_supplier_id"] is not None
    assert result["promoted_party_id"] is None
    assert len(result["created_item_ids"]) == 12
    assert result["linked_line_count"] == 12
    assert result["xml_download_url"] == f"/api/inward-bills/{bill_id}/xml"

    # masters really landed
    with SessionLocal() as s:
        sup = s.get(Party, result["created_supplier_id"])
        assert sup is not None
        assert sup.legal_name == "SUGAL FOODS"
        assert sup.role == "supplier"
        assert sup.source == "inward_bill"
        assert sup.source_ref == bill_id
        assert sup.last_txn_at is not None  # bumped to bill_date

        items = list(
            s.scalars(select(Item).where(Item.id.in_(result["created_item_ids"]))).all()
        )
        assert len(items) == 12
        for it in items:
            assert it.source == "auto_from_purchase"
            assert it.status == "unconfirmed"
            assert it.hsn_code == "21069092"
            assert it.last_purchase_rate is not None

        audit = s.scalar(
            select(AuditLog).where(
                AuditLog.entity == "inward_bill", AuditLog.entity_id == bill_id
            )
        )
        assert audit is not None
        assert audit.action == "approve"

    # XML downloads and is well-formed
    r = client.get(f"/api/inward-bills/{bill_id}/xml", headers=headers)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    root = etree.fromstring(r.content)
    assert root.tag == "ENVELOPE"
    # a purchase voucher
    vch = root.find(".//VOUCHER")
    assert vch is not None
    assert vch.get("VCHTYPE") == "Purchase"
    assert root.findtext(".//VOUCHERNUMBER") == "INV2526-5667"
    assert root.findtext(".//DATE") == "20250825"
    # UDF:METALERP_REF carries the inward_bill id for re-import detection
    udf = root.find(".//u:METALERP_REF", namespaces={"u": "urn:metalerp:tally-udf"})
    assert udf is not None and udf.text == f"ib_{bill_id}"
    # 12 inventory entries
    assert len(root.findall(".//ALLINVENTORYENTRIES.LIST")) == 12
    # a LEDGER create for the new supplier + 12 STOCKITEM creates
    assert root.find('.//LEDGER[@NAME="SUGAL FOODS"]') is not None
    assert len(root.findall(".//STOCKITEM")) == 12
    # party ledger name is the supplier
    assert "SUGAL FOODS" in {e.text for e in root.findall(".//LEDGERNAME")}
    # intra-state -> CGST + SGST ledger lines, no IGST
    ledger_names = {e.text for e in root.findall(".//LEDGERENTRIES.LIST/LEDGERNAME")}
    assert "CGST" in ledger_names and "SGST" in ledger_names
    assert "IGST" not in ledger_names
    assert "Round Off" in ledger_names


def test_reapprove_is_conflict(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    bill = _upload_sugal(client, headers, sugal_pdf_bytes)
    bid = bill["id"]
    assert client.post(f"/api/inward-bills/{bid}/approve", headers=headers).status_code == 200
    r2 = client.post(f"/api/inward-bills/{bid}/approve", headers=headers)
    assert r2.status_code == 422
    assert "already approved" in str(r2.json()["detail"])


def _tenant_of(client: TestClient, headers: dict[str, str]) -> str:
    return client.get("/api/auth/me", headers=headers).json()["tenant_id"]


def test_patch_overrides_a_line_match(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    tenant_id = _tenant_of(client, headers)

    with SessionLocal() as s:
        it = Item(
            tenant_id=tenant_id,
            name="Monin Mojito Mint Syrup 1L",
            name_normalized="monin mojito mint syrup 1l",
        )
        s.add(it)
        s.commit()
        item_id = it.id

    bill = _upload_sugal(client, headers, sugal_pdf_bytes)
    bid = bill["id"]

    r = client.patch(
        f"/api/inward-bills/{bid}",
        headers=headers,
        json={"lines": [{"sl_no": 1, "matched_item_id": item_id}]},
    )
    assert r.status_code == 200, r.text
    line1 = next(ln for ln in r.json()["lines"] if ln["sl_no"] == 1)
    assert line1["matched_item_id"] == item_id
    assert line1["match_method"] == "manual"
    assert line1["new_item_staged_json"] is None


def test_reject_blocks_approve(
    inward_client: tuple[TestClient, dict[str, str]],
    sugal_pdf_bytes: bytes,
    seeded_hsn: None,
) -> None:
    client, headers = inward_client
    bill = _upload_sugal(client, headers, sugal_pdf_bytes)
    bid = bill["id"]
    r = client.post(
        f"/api/inward-bills/{bid}/reject", headers=headers, json={"reason": "duplicate"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert client.post(f"/api/inward-bills/{bid}/approve", headers=headers).status_code == 422

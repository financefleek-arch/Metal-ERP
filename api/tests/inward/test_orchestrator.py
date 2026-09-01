"""run_extraction orchestrator — status transitions and the reconcile gate,
driven with a stubbed extractor so we don't need a zoo of fixture PDFs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ExtractionRun, InwardBill, Tenant, User
from app.models._mixins import InwardStatus
from app.services.inward import run_extraction as orch
from app.services.inward.extract_text import RawExtraction, RawLine


@pytest.fixture
def tenant_user() -> tuple[str, str]:
    with SessionLocal() as s:
        t = Tenant(legal_name="Orchestrator Co", ext_inward_import=True)
        s.add(t)
        s.flush()
        u = User(tenant_id=t.id, email="o@x.example.com", password_hash="x")
        s.add(u)
        s.commit()
        return t.id, u.id


def _good_extraction() -> RawExtraction:
    e = RawExtraction()
    e.supplier_name = "Test Supplier"
    e.supplier_gstin = "19BHBPK1450P1Z3"
    e.buyer_gstin = "19AALFR8182P1Z3"
    e.bill_no = "T-1"
    e.bill_date = "2025-08-25"
    e.place_of_supply_state_code = "19"
    e.taxable_total = Decimal("100.00")
    e.cgst_total = Decimal("9.00")
    e.sgst_total = Decimal("9.00")
    e.round_off = Decimal("0.00")
    e.grand_total = Decimal("118.00")
    e.lines = [RawLine(sl_no=1, description="Widget", hsn="21069092",
                       quantity=Decimal("1"), uom="Nos", unit_rate=Decimal("100.00"),
                       line_total=Decimal("100.00"))]
    e.field_confidence = {"a": 0.95, "b": 0.95}
    return e


def _mk_bill(tenant_id: str, user_id: str) -> str:
    with SessionLocal() as s:
        b = InwardBill(
            tenant_id=tenant_id,
            uploaded_by=user_id,
            source_filename="t.pdf",
            source_pdf_path="/nonexistent.pdf",
            status=InwardStatus.uploaded,
        )
        s.add(b)
        s.commit()
        return b.id


def test_reconciled_bill_lands_needs_review(
    tenant_user: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, uid = tenant_user
    monkeypatch.setattr(orch.einvoice_qr, "decode", lambda _p: None)
    monkeypatch.setattr(orch.extract_text, "extract", lambda _p: _good_extraction())
    bid = _mk_bill(tid, uid)

    with SessionLocal() as s:
        bill = s.get(InwardBill, bid)
        orch.run_extraction(s, bill)
        s.commit()

    with SessionLocal() as s:
        bill = s.get(InwardBill, bid)
        assert bill.status == InwardStatus.needs_review
        assert bill.reconciled is True
        assert bill.reconcile_discrepancy == Decimal("0.00")
        assert bill.extraction_method == "table"
        assert len(bill.lines) == 1
        runs = s.scalars(
            select(ExtractionRun).where(ExtractionRun.inward_bill_id == bid)
        ).all()
        assert len(runs) == 1 and runs[0].ok is True


def test_broken_totals_lands_needs_review_unreconciled(
    tenant_user: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, uid = tenant_user
    bad = _good_extraction()
    bad.grand_total = Decimal("999.00")  # way off
    monkeypatch.setattr(orch.einvoice_qr, "decode", lambda _p: None)
    monkeypatch.setattr(orch.extract_text, "extract", lambda _p: bad)
    bid = _mk_bill(tid, uid)

    with SessionLocal() as s:
        bill = s.get(InwardBill, bid)
        orch.run_extraction(s, bill)
        s.commit()

    with SessionLocal() as s:
        bill = s.get(InwardBill, bid)
        assert bill.status == InwardStatus.needs_review
        assert bill.reconciled is False
        assert bill.reconcile_discrepancy == Decimal("881.00")
        # approve gate must block on it
        from app.services.inward.approve import approve_gate

        blockers = approve_gate(bill)
        assert any("reconcile" in b for b in blockers)


def test_extractor_exception_lands_error_status(
    tenant_user: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, uid = tenant_user

    def _boom(_p: str) -> RawExtraction:
        raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(orch.einvoice_qr, "decode", lambda _p: None)
    monkeypatch.setattr(orch.extract_text, "extract", _boom)
    bid = _mk_bill(tid, uid)

    with SessionLocal() as s:
        bill = s.get(InwardBill, bid)
        orch.run_extraction(s, bill)
        s.commit()

    with SessionLocal() as s:
        bill = s.get(InwardBill, bid)
        assert bill.status == InwardStatus.error
        assert "corrupt pdf" in (bill.error_message or "")

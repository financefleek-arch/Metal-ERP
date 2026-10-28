"""Voucher capture (plan P0): queue one read-only Export for the agent, record where the
unparsed XML landed. No parsing, no ledger writes."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AgentOutboxItem, TallyCompany, TallySyncJob
from app.services.tally.voucher_capture import (
    assert_no_capture_in_flight,
    build_bills_receivable_request,
    build_vouchers_request,
    enqueue_voucher_capture,
    is_export_request,
)
from tests.test_tally_connector import (  # noqa: F401  (autouse stubs + helpers)
    _admin_token,
    _link_company,
    _make_firm,
    _stub_installer_build,
    _stub_r2,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_vouchers_request_shape() -> None:
    xml = build_vouchers_request("M/s A & B", date(2025, 4, 1), date(2025, 4, 30))
    assert "<SVFROMDATE>20250401</SVFROMDATE><SVTODATE>20250430</SVTODATE>" in xml
    assert "<SVCURRENTCOMPANY>M/s A &amp; B</SVCURRENTCOMPANY>" in xml  # escaped
    assert is_export_request(xml)
    with pytest.raises(ValueError):
        build_vouchers_request("c", date(2025, 5, 1), date(2025, 4, 1))


def test_bills_receivable_request_shape() -> None:
    xml = build_bills_receivable_request("", date(2026, 10, 28))
    assert "<ID>Bills Receivable</ID>" in xml
    assert "SVCURRENTCOMPANY" not in xml  # no company -> Tally uses the open one
    assert is_export_request(xml)


@pytest.mark.parametrize(
    "xml,expected",
    [
        ("<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER></ENVELOPE>", True),
        ("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER></ENVELOPE>", False),
        ("<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><IMPORTDATA/></ENVELOPE>", False),
        ("<ENVELOPE/>", False),
    ],
)
def test_only_export_is_accepted(xml: str, expected: bool) -> None:
    assert is_export_request(xml) is expected


def test_capture_flow(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)

    xml = build_vouchers_request("M/s F1a Traders", date(2025, 4, 1), date(2025, 4, 30))
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == firm_id))
        assert_no_capture_in_flight(s, firm_id)
        job = enqueue_voucher_capture(s, company, label="vouchers-apr25", request_xml=xml)
        job_id = job.id
        s.commit()

        # a second capture is refused while this one is in flight
        with pytest.raises(HTTPException) as exc:
            assert_no_capture_in_flight(s, firm_id)
        assert exc.value.status_code == 409

        outbox = s.scalar(select(AgentOutboxItem).where(AgentOutboxItem.shop_id == shop_id))
        assert outbox.payload["action"] == "pull_vouchers"
        assert outbox.payload["label"] == "vouchers-apr25"
        assert outbox.payload["export_xml"] == xml

    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key},
        json={
            "status": "ok",
            "r2_key": f"{shop_id}/20261028_vouchers-apr25.xml",
            "tally_response": '{"bytes": 4096, "ms": 812}',
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.r2_key.endswith("vouchers-apr25.xml")
        assert job.counts == {"label": "vouchers-apr25", "bytes": 4096, "ms": 812}
        assert_no_capture_in_flight(s, firm_id)  # finished, so no longer blocks


def test_capture_error_is_recorded(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    shop_id, key = _link_company(client, tok, firm_id)
    xml = build_bills_receivable_request("", date(2026, 10, 28))
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == firm_id))
        job_id = enqueue_voucher_capture(s, company, label="bills", request_xml=xml).id
        s.commit()

    r = client.post(
        f"/api/tally-agent/jobs/{job_id}/result",
        headers={"X-Shop-Key": key},
        json={"status": "error", "error": "Tally did not answer in time. Try a shorter period."},
    )
    assert r.status_code == 200
    with SessionLocal() as s:
        job = s.get(TallySyncJob, job_id)
        assert job.status == "error"
        assert "shorter period" in job.error


def test_enqueue_rejects_import_and_bad_label(client: TestClient) -> None:
    tok = _admin_token(client)
    firm_id = _make_firm(client, tok)
    _link_company(client, tok, firm_id)
    with SessionLocal() as s:
        company = s.scalar(select(TallyCompany).where(TallyCompany.tenant_id == firm_id))
        with pytest.raises(ValueError):
            enqueue_voucher_capture(
                s, company, label="ok",
                request_xml="<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER></ENVELOPE>",
            )
        with pytest.raises(ValueError):
            enqueue_voucher_capture(
                s, company, label="../etc", request_xml=build_bills_receivable_request("", date.today())
            )

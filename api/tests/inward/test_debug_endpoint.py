"""The dev-only debug endpoint: PDF in -> Tally XML out, no auth, no DB."""

from __future__ import annotations

from fastapi.testclient import TestClient
from lxml import etree

from app.main import app
from tests.inward.conftest import SUGAL_PDF


def test_debug_form_served() -> None:
    c = TestClient(app)
    r = c.get("/api/inward-debug")
    assert r.status_code == 200
    assert "PDF → Tally XML" in r.text


def test_debug_extract_json() -> None:
    c = TestClient(app)
    r = c.post(
        "/api/inward-debug/extract",
        files={"file": ("s.pdf", SUGAL_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["supplier_gstin"] == "19BHBPK1450P1Z3"
    assert body["bill_no"] == "INV2526-5667"
    assert body["reconciled"] is True
    assert len(body["lines"]) == 12
    assert body["totals"]["grand_total"] == "42445.00"


def test_debug_xml_download() -> None:
    c = TestClient(app)
    r = c.post(
        "/api/inward-debug/xml",
        files={"file": ("s.pdf", SUGAL_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200
    assert r.headers["x-reconciled"] == "true"
    assert 'filename="inward-INV2526-5667.xml"' in r.headers["content-disposition"]

    root = etree.fromstring(r.content)
    assert root.tag == "ENVELOPE"
    assert root.find(".//VOUCHER").get("VCHTYPE") == "Purchase"
    assert len(root.findall(".//ALLINVENTORYENTRIES.LIST")) == 12
    assert len(root.findall(".//STOCKITEM")) == 12  # every line is a new stock item
    assert root.findtext(".//VOUCHERNUMBER") == "INV2526-5667"


def test_debug_rejects_non_pdf() -> None:
    c = TestClient(app)
    r = c.post(
        "/api/inward-debug/xml",
        files={"file": ("x.txt", b"not a pdf", "text/plain")},
    )
    assert r.status_code == 415

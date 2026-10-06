"""One front door: is this PDF a bill or a price list?"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import document_kind
from tests.catalog.conftest import SAMPLE_PDF


def test_a_bill_is_a_bill(sugal_pdf_bytes: bytes) -> None:
    g = document_kind.guess(sugal_pdf_bytes)
    assert g.kind == "bill" and any("GSTIN" in r for r in g.reasons)


def test_a_price_list_is_a_price_list() -> None:
    g = document_kind.guess(SAMPLE_PDF.read_bytes())
    assert g.kind == "price_list" and any("price lines" in r for r in g.reasons)


def test_nonsense_is_unknown() -> None:
    assert document_kind.guess(b"%PDF-not really").kind == "unknown"


def test_detect_endpoint_respects_the_firms_flags(
    inward_client: tuple[TestClient, dict[str, str]], sugal_pdf_bytes: bytes
) -> None:
    client, h = inward_client  # only the inward flag is on
    r = client.post(
        "/api/documents/detect",
        headers=h,
        files={"file": ("a.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # it looks like a price list, but this firm only does bills: never suggest what it cannot do
    assert body["kind"] == "bill"
    assert body["bills_enabled"] is True and body["price_lists_enabled"] is False
    ok = client.post(
        "/api/documents/detect",
        headers=h,
        files={"file": ("b.pdf", sugal_pdf_bytes, "application/pdf")},
    )
    assert ok.json()["kind"] == "bill"
    bad = client.post(
        "/api/documents/detect", headers=h, files={"file": ("c.pdf", b"hello", "application/pdf")}
    )
    assert bad.status_code == 415

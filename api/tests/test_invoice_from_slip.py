"""Slip-capture OCR pilot — POST /api/invoices/from-slip.

The Anthropic vision call is mocked (`extract_slip`) so this runs fast, free,
and without a real API key; it exercises the real wiring end to end: upload
-> extraction -> item/party resolution (`resolve_item`/`resolve_party`,
unmocked) -> a draft invoice, same shape as any other draft.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ocr.extract_slip import ExtractedLine, SlipExtraction, SlipExtractionError

# A 1x1 PNG — content doesn't matter since extract_slip is mocked; it only
# has to pass the real media-type sniff in extract_slip._media_type.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, email: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _mock_extraction(monkeypatch: pytest.MonkeyPatch, result: SlipExtraction) -> None:
    monkeypatch.setattr("app.routers.invoices.extract_slip", lambda _data: result)


def test_slip_with_known_item_resolves_and_creates_draft(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "slip1@x.example.com"))
    item = client.post("/api/items", headers=h, json={"name": "Monin syrup"})
    assert item.status_code == 201, item.text

    _mock_extraction(
        monkeypatch,
        SlipExtraction(
            party_name=None,
            lines=[
                ExtractedLine(
                    description="Monin syrup", quantity=Decimal("24"), uom="nos", rate=Decimal("100")
                )
            ],
        ),
    )

    r = client.post(
        "/api/invoices/from-slip",
        headers=h,
        files={"file": ("slip.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["invoice"]["status"] == "draft"
    assert len(body["invoice"]["lines"]) == 1
    line = body["invoice"]["lines"][0]
    assert line["item_id"] == item.json()["id"]
    assert line["quantity"] == "24"
    assert line["unit_rate"] == "100"

    assert body["line_reviews"] == [{"sl_no": 1, "needs_review": False, "review_reason": None}]
    assert body["party_guess_name"] is None
    assert body["party_needs_review"] is False


def test_slip_with_unknown_item_flags_for_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "slip2@x.example.com"))

    _mock_extraction(
        monkeypatch,
        SlipExtraction(
            party_name="Sugal Foods",
            lines=[
                ExtractedLine(
                    description="Totally unseen gadget", quantity=Decimal("3"), uom=None, rate=None
                )
            ],
            notes="handwriting faint on line 1",
        ),
    )

    r = client.post(
        "/api/invoices/from-slip",
        headers=h,
        files={"file": ("slip.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    line = body["invoice"]["lines"][0]
    assert line["item_id"] is None  # staged as free text, not silently matched
    assert body["line_reviews"][0]["needs_review"] is True
    assert body["party_guess_name"] == "Sugal Foods"
    assert body["party_needs_review"] is True  # no such party exists yet
    assert body["notes"] == "handwriting faint on line 1"


def test_slip_extraction_failure_is_a_422_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "slip3@x.example.com"))

    def _boom(_data: bytes) -> SlipExtraction:
        raise SlipExtractionError("Vision call failed: network error")

    monkeypatch.setattr("app.routers.invoices.extract_slip", _boom)

    r = client.post(
        "/api/invoices/from-slip",
        headers=h,
        files={"file": ("slip.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 422, r.text

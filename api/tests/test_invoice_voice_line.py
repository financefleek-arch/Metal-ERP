"""Speech-invoice-capture pilot — POST /api/invoices/voice-line.

The Deepgram call is mocked (`transcribe`) so this runs fast, free, and
without a real API key; it exercises the real wiring: transcript -> the
deterministic parser (unmocked) -> item resolution (unmocked) -> a resolved
line, no invoice side effects.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.speech.transcribe import Transcript, TranscriptionError

_TINY_AUDIO = b"\x1aE\xdf\xa3fake-webm-bytes-not-real-audio"


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


def _mock_transcript(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(
        "app.routers.invoices.transcribe",
        lambda _data, _ct, **_kw: Transcript(text=text, confidence=0.95),
    )


def test_voice_line_known_item_resolves(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "voice1@x.example.com"))
    item = client.post("/api/items", headers=h, json={"name": "Monin syrup"})
    assert item.status_code == 201, item.text

    _mock_transcript(monkeypatch, "Monin syrup do dozen sau rupaye")

    r = client.post(
        "/api/invoices/voice-line",
        headers=h,
        files={"file": ("line.webm", _TINY_AUDIO, "audio/webm")},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["item_id"] == item.json()["id"]
    assert body["quantity"] == "2"
    assert body["uom"] == "doz"
    assert body["unit_rate"] == "100"
    assert body["needs_review"] is False


def test_voice_line_unknown_item_flags_for_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "voice2@x.example.com"))
    _mock_transcript(monkeypatch, "totally unseen gadget 3 nos")

    r = client.post(
        "/api/invoices/voice-line",
        headers=h,
        files={"file": ("line.webm", _TINY_AUDIO, "audio/webm")},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["item_id"] is None
    assert body["needs_review"] is True


def test_voice_line_no_quantity_heard_flags_for_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "voice3@x.example.com"))
    client.post("/api/items", headers=h, json={"name": "chimta"})
    _mock_transcript(monkeypatch, "chimta")

    r = client.post(
        "/api/invoices/voice-line",
        headers=h,
        files={"file": ("line.webm", _TINY_AUDIO, "audio/webm")},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["needs_review"] is True
    assert body["quantity"] is None


def test_voice_line_transcription_failure_is_422_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _h(_register(client, "voice4@x.example.com"))

    def _boom(_data: bytes, _ct: str, **_kw: object) -> Transcript:
        raise TranscriptionError("Couldn't hear anything in that recording — try again.")

    monkeypatch.setattr("app.routers.invoices.transcribe", _boom)

    r = client.post(
        "/api/invoices/voice-line",
        headers=h,
        files={"file": ("line.webm", _TINY_AUDIO, "audio/webm")},
    )
    assert r.status_code == 422, r.text

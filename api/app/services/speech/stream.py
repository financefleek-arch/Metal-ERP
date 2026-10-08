"""Real-time speech-invoice-capture — opens the Deepgram side of the
WebSocket proxy in app/routers/voice_stream.py, so the Deepgram secret key
never reaches the browser.

Browser --(webm/opus audio chunks)--> us --(same)--> Deepgram
Deepgram --(partial/final transcript JSON)--> us --(same, unmodified)--> Browser

The browser's `MediaRecorder` already produces a webm/opus container, which
Deepgram decodes automatically — no raw PCM / sample-rate wrangling needed
(see Deepgram's own guidance: containerised audio should NOT set encoding/
sample_rate query params).
"""

from __future__ import annotations

from urllib.parse import quote

import websockets
from websockets.asyncio.client import ClientConnection

from app.config import get_settings

_DEEPGRAM_STREAM_URL = "wss://api.deepgram.com/v1/listen"


class StreamNotConfigured(Exception):
    pass


def _deepgram_url(keyterms: list[str]) -> str:
    params = [
        ("model", "nova-3"),
        ("language", "multi"),
        ("smart_format", "true"),
        ("numerals", "true"),
        ("interim_results", "true"),
    ]
    params.extend(("keyterm", k) for k in keyterms)
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    return f"{_DEEPGRAM_STREAM_URL}?{query}"


async def open_deepgram_stream(keyterms: list[str]) -> ClientConnection:
    settings = get_settings()
    if not settings.deepgram_secret_key:
        raise StreamNotConfigured("Speech capture is not configured (no Deepgram key).")
    return await websockets.connect(
        _deepgram_url(keyterms),
        additional_headers={"Authorization": f"Token {settings.deepgram_secret_key}"},
    )

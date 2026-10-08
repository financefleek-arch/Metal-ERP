"""Speech-invoice-capture pilot — one push-to-talk voice clip -> a transcript,
via Deepgram's prerecorded API (nova-3, language=multi for Hindi/Hinglish/
English code-switch).

Not a live stream: the operator records one line, releases the button, this
is called once per clip. Confirmed quality on mixed-language shop-floor
speech from earlier Fleek testing — see speech-invoice-capture-backlog.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import get_settings

_DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
MAX_AUDIO_BYTES = 10 * 1024 * 1024


class TranscriptionError(Exception):
    """The Deepgram call failed, or the clip couldn't be read."""


@dataclass
class Transcript:
    text: str
    confidence: float | None


# Nova-3's keyterm prompting budget is 500 tokens total across all terms
# per request (Deepgram docs) — cap the list well under that so a long tail
# of item names can't silently truncate the ones that matter most.
MAX_KEYTERMS = 100


def transcribe(
    audio_bytes: bytes, content_type: str, keyterms: list[str] | None = None
) -> Transcript:
    if not audio_bytes:
        raise TranscriptionError("Empty recording.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise TranscriptionError("That recording is too large.")

    settings = get_settings()
    if not settings.deepgram_secret_key:
        raise TranscriptionError("Speech capture is not configured (no Deepgram key).")

    params: dict[str, str | list[str]] = {
        "model": "nova-3",
        "language": "multi",
        "smart_format": "true",
        "numerals": "true",
    }
    if keyterms:
        # Nova-3 only (silently ignored on other models); plain terms, no
        # weights — repeat the query param once per term, httpx handles a
        # list value as repeated params.
        params["keyterm"] = keyterms[:MAX_KEYTERMS]

    try:
        resp = httpx.post(
            _DEEPGRAM_URL,
            params=params,
            headers={
                "Authorization": f"Token {settings.deepgram_secret_key}",
                "Content-Type": content_type,
            },
            content=audio_bytes,
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Transcription call failed: {exc}") from exc

    body = resp.json()
    try:
        alt = body["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError) as exc:
        raise TranscriptionError("Unexpected response from Deepgram.") from exc

    text = (alt.get("transcript") or "").strip()
    if not text:
        raise TranscriptionError("Couldn't hear anything in that recording — try again.")
    return Transcript(text=text, confidence=alt.get("confidence"))

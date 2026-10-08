"""Slip-capture OCR pilot — a photographed kachcha slip -> structured sales-
invoice lines, via one Claude vision call.

This is the sales-side counterpart to the inward (purchase) pipeline: the
shop already writes every sale on a paper slip before it goes into Tally by
hand (confirmed with the user 2026-10-08). The slip IS the operator's
existing interface; this module just reads it, same spirit as `inward`'s
PDF/QR extraction reads a supplier's printed bill.

Scope for the pilot — deliberately NOT doing yet:
  - no confidence-threshold auto-accept: every slip always opens in the
    draft editor for a human look (same posture as inward's "first pass
    always lands in review" — see run_extraction.py);
  - no correction-feedback / alias-learning loop (that's the backlog's
    "compounding edge", v2 concern);
  - no cost metering/quota.

Call shape: `extract_slip(image_bytes) -> SlipExtraction`, then the router
resolves each line against the item master via the existing
`item_resolution.resolve_item` ladder (fuzzy/alias/exact — no new matching
logic) and the party guess (if any) via `party_resolution.resolve_party`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from anthropic import Anthropic

from app.config import get_settings

MODEL = "claude-opus-5-5"
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_SYSTEM_PROMPT = """You read photographed handwritten paper slips ("kachcha \
slips") from an Indian metal/steel-trade shop counter, written by the \
shop operator to record one sale before it is entered into accounting \
software. Extract every line item plus the customer name if written.

Handwriting is often rough; item names are frequently abbreviated Hindi/ \
Hinglish trade terms (e.g. "patra", "kadai", "tar", "chadar", "gauge" \
numbers). Numbers may use Indian number words or Devanagari numerals. \
Rates are usually per-piece or per-kg in rupees.

Return ONLY a JSON object, no prose, matching exactly this shape:
{
  "party_name": string or null,   // customer name if legible on the slip, else null
  "lines": [
    {
      "description": string,      // the item text as best transcribed/expanded, not re-translated
      "quantity": string,         // a plain decimal number as a string, e.g. "24" or "2.5"
      "uom": string or null,      // unit word as written (e.g. "dozen", "kg", "pcs", "nos")
      "rate": string or null      // a plain decimal number as a string, or null if not written
    }
  ],
  "notes": string or null         // anything illegible, crossed out or ambiguous worth a look
}

If a field is illegible or missing, use null rather than guessing a value. \
Never invent a line that is not on the slip."""


class SlipExtractionError(Exception):
    """The vision call failed, or returned something that isn't the expected shape."""


@dataclass
class ExtractedLine:
    description: str
    quantity: Decimal | None
    uom: str | None
    rate: Decimal | None


@dataclass
class SlipExtraction:
    party_name: str | None
    lines: list[ExtractedLine] = field(default_factory=list)
    notes: str | None = None


def _to_decimal(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _media_type(image_bytes: bytes) -> str:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    raise SlipExtractionError("Unrecognised image format (use JPEG, PNG or WebP).")


def extract_slip(image_bytes: bytes) -> SlipExtraction:
    """One Claude vision call: a photographed slip -> `SlipExtraction`.

    Raises `SlipExtractionError` on a transport/parse failure — the caller
    should surface this as "couldn't read that slip, try retaking the photo
    or enter it by hand", not a 500.
    """
    if not image_bytes:
        raise SlipExtractionError("Empty image.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise SlipExtractionError("That photo is too large.")
    media_type = _media_type(image_bytes)

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SlipExtractionError("Slip capture is not configured (no Anthropic API key).")

    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        import base64
        from typing import Any

        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        content: list[Any] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {"type": "text", "text": "Extract this slip as the specified JSON."},
        ]
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # network/API error — never a 500 to the caller
        raise SlipExtractionError(f"Vision call failed: {exc}") from exc

    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SlipExtractionError(f"Could not parse the model's response: {exc}") from exc

    lines = []
    for raw_line in payload.get("lines") or []:
        desc = (raw_line.get("description") or "").strip()
        if not desc:
            continue
        lines.append(
            ExtractedLine(
                description=desc,
                quantity=_to_decimal(raw_line.get("quantity")),
                uom=(raw_line.get("uom") or None),
                rate=_to_decimal(raw_line.get("rate")),
            )
        )

    return SlipExtraction(
        party_name=(payload.get("party_name") or None),
        lines=lines,
        notes=(payload.get("notes") or None),
    )

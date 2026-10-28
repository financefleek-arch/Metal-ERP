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

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from anthropic import Anthropic

from app.config import get_settings

MODEL = "claude-haiku-5-5"
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_SYSTEM_PROMPT = """You read photographed handwritten paper slips ("kachcha \
slips") from an Indian metal/steel-trade shop counter, written by the \
shop operator to record one sale before it is entered into accounting \
software. Extract every line item plus the customer name if written.

Handwriting is often rough; item names are frequently abbreviated Hindi/ \
Hinglish trade terms (e.g. "patra", "kadai", "tar", "chadar", "gauge" \
numbers). Numbers may use Indian number words or Devanagari numerals. \
Rates are usually per-piece or per-kg in rupees.

The slip may also be a printed software bill photographed off a screen or \
paper. Then: quantity written like "1X5=5" or "144X0=144" is packs x pieces = \
total, so use the number AFTER the "=" as the quantity; rate is the "Rate" \
column (before discount), not the net price or amount.

If a field is illegible or missing, use null rather than guessing a value. \
Never invent a line that is not on the slip. Report via the tool."""

_TOOL = {
    "name": "record_slip",
    "description": "Record the lines read from the slip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "party_name": {"type": ["string", "null"], "description": "Customer name if legible, else null"},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Item text as written"},
                        "quantity": {"type": ["string", "null"], "description": "Plain decimal, e.g. 24"},
                        "uom": {"type": ["string", "null"], "description": "Unit as written, e.g. dozen"},
                        "rate": {"type": ["string", "null"], "description": "Plain decimal or null"},
                    },
                    "required": ["description"],
                },
            },
            "notes": {"type": ["string", "null"], "description": "Anything illegible or ambiguous"},
        },
        "required": ["lines"],
    },
}


class SlipExtractionError(Exception):
    """The vision call failed, or returned something that isn't the expected shape.

    `input_tokens`/`output_tokens` are set when the API answered but the answer was
    unusable: that call was still billed, so the usage counter records it.
    """

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


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
    input_tokens: int = 0
    output_tokens: int = 0


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
            {"type": "text", "text": "Read this slip and report it by calling the record_slip tool."},
        ]
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # network/API error — never a 500 to the caller
        raise SlipExtractionError(f"Vision call failed: {exc}") from exc

    in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
    if resp.stop_reason == "max_tokens":
        raise SlipExtractionError(
            "That slip has too many lines to read in one go; try two photos.", in_tok, out_tok
        )
    payload = next((b.input for b in resp.content if b.type == "tool_use"), None)
    if not isinstance(payload, dict):
        raise SlipExtractionError("Could not read that slip, try retaking the photo.", in_tok, out_tok)

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
        input_tokens=in_tok,
        output_tokens=out_tok,
    )

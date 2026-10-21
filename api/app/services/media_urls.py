"""Signed URLs for media images. Kept separate from `media.py` so schemas can import it without
pulling in Pillow and the models."""

from __future__ import annotations

from app.config import get_settings
from app.services.catalog import image_url

SCOPE = "media"


def card_url(media_id: str, *, days: int = 14) -> str:
    """A full https address for a WhatsApp card picture.

    Meta fetches it when the message is sent, which for a paced campaign can be hours later, so it stays
    valid for `days` days (the page's own links last 1 to 2 hours)."""
    query = image_url.sign_query(media_id, scope=SCOPE, hours=days * 24)
    return f"{get_settings().base_url.rstrip('/')}/api/media/{media_id}/card?{query}"


def media_url(media_id: str, variant: str = "photo") -> str:
    return f"/api/media/{media_id}/{variant}?{image_url.sign_query(media_id, scope=SCOPE)}"

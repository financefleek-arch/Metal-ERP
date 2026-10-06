"""Signed URLs for media images. Kept separate from `media.py` so schemas can import it without
pulling in Pillow and the models."""

from __future__ import annotations

from app.services.catalog import image_url

SCOPE = "media"


def media_url(media_id: str, variant: str = "photo") -> str:
    return f"/api/media/{media_id}/{variant}?{image_url.sign_query(media_id, scope=SCOPE)}"

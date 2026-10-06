"""Spot a price-list crop that is probably not a usable photo of the product.

The PDF's pictures are cut out by position, so a bad crop is possible: a thumbnail, a sliver of a
banner, an empty box. This only raises a flag for a person to look at, never changes anything.
"""

from __future__ import annotations

import io

from PIL import Image, ImageStat, UnidentifiedImageError

MIN_SIDE = 120  # px; below this a photo will look blurry on a printed catalog
MAX_RATIO = 3.0
BLANK_STDDEV = 6.0  # grey levels: a flat colour


def check(data: bytes) -> str | None:
    """None when it looks fine, else small | odd_shape | blank | unreadable."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        return "unreadable"
    w, h = img.size
    if min(w, h) < MIN_SIDE:
        return "small"
    ratio = max(w, h) / max(1, min(w, h))
    if ratio > MAX_RATIO:
        return "odd_shape"
    if max(ImageStat.Stat(img.convert("L")).stddev) < BLANK_STDDEV:
        return "blank"
    return None

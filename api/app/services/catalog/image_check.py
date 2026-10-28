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


_LOOK = 256  # px: a blank crop is just as flat at this size, and it decodes in a few KB


def check(data: bytes) -> str | None:
    """None when it looks fine, else small | odd_shape | blank | unreadable.

    Size and shape come from the header; only a small greyscale decode is made for the blank test, so a
    12-megapixel photo costs a few KB of memory here, not tens of MB.
    """
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size  # the real size, read from the header before any decode
        img.draft("L", (_LOOK, _LOOK))  # JPEG: decode at 1/2, 1/4 or 1/8 scale; others ignore it
        img.load()  # still has to be readable, whatever its size
    except (UnidentifiedImageError, OSError):
        return "unreadable"
    if min(w, h) < MIN_SIDE:
        return "small"
    if max(w, h) / max(1, min(w, h)) > MAX_RATIO:
        return "odd_shape"
    img.thumbnail((_LOOK, _LOOK))
    if max(ImageStat.Stat(img.convert("L")).stddev) < BLANK_STDDEV:
        return "blank"
    return None

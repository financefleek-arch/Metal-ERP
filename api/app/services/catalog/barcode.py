"""Code 128 barcodes for item codes.

`modules(code)` returns the bar pattern as a string of 1 (bar) and 0 (space) modules,
without quiet zones. The same string drives the printed labels (vector bars in the
PDF) and the small barcode on each review row (drawn as SVG in the browser), so what
the user sees is exactly what is printed.
"""

from __future__ import annotations

from functools import lru_cache

from barcode import Code128

# Quiet zone each side, in modules. The Code 128 spec asks for 10.
QUIET_MODULES = 10
_MAX_LEN = 40


def _validate(code: str) -> None:
    if not code or len(code) > _MAX_LEN:
        raise ValueError(f"barcode text must be 1 to {_MAX_LEN} characters")
    if any(not (32 <= ord(c) <= 126) for c in code):
        raise ValueError("barcode text must be printable ASCII")


@lru_cache(maxsize=8192)
def modules(code: str) -> str:
    """Bar/space modules for `code` ('1' = bar, '0' = space), no quiet zone."""
    _validate(code)
    built: str = Code128(code).build()[0]
    return built


def runs(pattern: str) -> list[tuple[int, int]]:
    """(start, length) of each run of bars in `pattern`, for drawing."""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "1":
            j = i
            while j < len(pattern) and pattern[j] == "1":
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out

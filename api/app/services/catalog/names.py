"""Name cleanup for catalog items: display name, carton qty, brand, size.

Supplier names carry packing noise ("... 6 PC COL BOX 12 SET CTN"). The display
name drops the carton phrase and the trailing box type, fixes the shouting
capitals, and keeps sizes and brands readable. The raw name is always kept.
"""

from __future__ import annotations

import re
from collections import Counter

# Tokens kept upper-case when they are units/packing words or short codes.
_KEEP_UPPER = {"ML", "LTR", "L", "PC", "PCS", "SET", "CTN", "KFT", "CM", "MM", "PET", "SS", "GK"}
_NOT_BRANDS = {"SET", "PCS", "PC", "BOX", "THE", "AND", "FOR", "WITH", "NEW", "BIG", "SMALL"}

_CARTON = re.compile(r"[-\s]*(\d+)\s*SETS?\s*CTN\b", re.IGNORECASE)
_TRAIL_BOX = re.compile(
    r"\b(?:IN\s+)?(?:COL|COLOR|COLOUR|BROWN|KFT|KRAFT|GIFT|WHITE|PLAIN)?\s*BOX\b\s*-?$",
    re.IGNORECASE,
)
_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ML|LTRS?|LTR|L|CM|MM|INCH|IN)\b|(\d+(?:\.\d+)?)\s*\"\s*(?:INCH)?",
    re.IGNORECASE,
)


def display_name(
    raw: str, supplier_code: str, brands: set[str] | None = None
) -> tuple[str, int | None]:
    """(clean display name, carton qty). Never returns an empty name."""
    name = _strip_code(raw, supplier_code)

    carton: int | None = None
    m = _CARTON.search(name)
    if m:
        carton = int(m.group(1))
        name = name[: m.start()] + name[m.end() :]
    name = _TRAIL_BOX.sub("", name.strip())
    name = re.sub(r"\s+", " ", name).strip(" -,")

    pretty = _pretty(name, brands or set())
    return (pretty or raw.strip() or supplier_code), carton


_HYPHEN_SPACE = re.compile(r"-\s+")


def _strip_code(text: str, code: str) -> str:
    """Remove the supplier code from the start and end of a name (case-insensitive).

    Tolerates a stray space after a hyphen in the text ("TXZ-02- L4-HA" for code
    "TXZ-02-L4-HA"). Plain string work: a per-item regex would thrash the regex cache.
    """
    c = code.upper()
    t = text.strip()
    for variant in (t, _HYPHEN_SPACE.sub("-", t)):
        u = variant.upper()
        if u.startswith(c) and (len(u) == len(c) or not u[len(c)].isalnum()):
            t = variant[len(c) :].lstrip(" 	-:")
            break
    for variant in (t, _HYPHEN_SPACE.sub("-", t)):
        u = variant.upper()
        if u.endswith(c) and (len(u) == len(c) or u[-len(c) - 1].isspace()):
            t = variant[: len(variant) - len(c)].rstrip()
            break
    return t


def _pretty(name: str, brands: set[str]) -> str:
    out: list[str] = []
    for tok in name.split():
        bare = re.sub(r"[^A-Za-z]", "", tok)
        if bare.upper() in brands or bare.upper() in _KEEP_UPPER:
            out.append(tok.upper() if tok.isalpha() else tok)
        elif tok.isalpha() and tok.isupper() and len(tok) <= 2:
            out.append(tok)  # short initialisms: DS, KD, WD
        elif any(c.isdigit() for c in tok) or not tok.isalpha():
            out.append(tok)  # sizes, codes, "5\"", "(1.9L"
        elif tok.isupper() or tok.islower():
            out.append(tok.capitalize())
        else:
            out.append(tok)  # already mixed case
    return " ".join(out)


def infer_brands(raw_names: list[str], supplier_codes: list[str]) -> set[str]:
    """Brands = tokens that START many names and almost never appear anywhere else.

    Data-driven, so it works for any supplier. "DELI 210 ML ..." / "DELI 6 PC ...":
    DELI leads many names and is rarely mid-name. A description word like GLASS or
    JAR also leads some names but shows up everywhere else too, so the lead/total
    ratio rejects it. The count threshold scales with the catalog size.
    """
    leading: Counter[str] = Counter()
    overall: Counter[str] = Counter()
    for raw, code in zip(raw_names, supplier_codes, strict=True):
        toks = _strip_code(raw, code).split()
        for t in toks:
            overall[re.sub(r"[^A-Za-z]", "", t).upper()] += 1
        if toks and toks[0].isalpha():
            tok = toks[0].upper()
            if len(tok) >= 3 and tok not in _NOT_BRANDS:
                leading[tok] += 1
    n = len(raw_names)
    threshold = max(3, round(n * 0.02))
    return {
        t for t, c in leading.items() if c >= threshold and c / max(overall[t], 1) >= 0.8
    }


def brand_of(raw: str, supplier_code: str, brands: set[str]) -> str | None:
    toks = _strip_code(raw, supplier_code).split()
    if not toks:
        return None
    tok = re.sub(r"[^A-Za-z]", "", toks[0]).upper()
    return tok if tok in brands else None


def size_of(name: str) -> str | None:
    m = _SIZE.search(name)
    if m is None:
        return None
    if m.group(1):
        unit = m.group(2).upper()
        if unit.startswith("LTR") or unit == "L":
            unit = "LTR"
        elif unit == "IN":
            unit = "INCH"
        return f"{m.group(1)} {unit}"
    return f'{m.group(3)}"'

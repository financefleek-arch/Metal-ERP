"""Rules-first parser for terse trade shorthand on inward bills and the
billing type-ahead.

`parse_product_line("Dhara Kettly 10cup 6PC -> 425")` ->
    ParsedLine(brand="Dhara Kettle", product="", sku=None, size="10cup",
               size_kind="cup", size_sort=10.0, rate_mode="piece",
               qty=6.0, rate=425.0, confidence=0.9)

Pure. No I/O. The caller passes the tenant's brand list (from item_category
names) and synonym map. The LLM residue path is the caller's problem — this
module just returns a low `confidence` and its best guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models._mixins import RateMode

# --------------------------------------------------------------------------
# size grammar — first match wins
# --------------------------------------------------------------------------

_SIZE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cup", re.compile(r"\b(\d+(?:\.\d+)?)\s*cup\b", re.I)),
    ("litre", re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:l(?:tr|itre)?)\b", re.I)),
    ("nxn", re.compile(r"\b(\d+(?:\.\d+)?)\s*[xX*]\s*(\d+(?:\.\d+)?)\b")),
    ("no", re.compile(r"\bno\.?\s*(\d+)\b", re.I)),
    ("gauge", re.compile(r"\b(\d+)\s*(?:g|gauge)\b", re.I)),
    ("mm", re.compile(r"\b(\d+(?:\.\d+)?)\s*mm\b", re.I)),
]

# a bare number that's plausibly a size (short, integer-ish), only used as a
# last resort once brand/qty/rate have been claimed.
_BARE_NUM = re.compile(r"\b(\d{1,3})\b")

# --------------------------------------------------------------------------
# quantity / rate / rate-mode
# --------------------------------------------------------------------------

# "6PC", "3 PC", "50 NUG", "2 SET"
_QTY_PIECE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:pc|pcs|nug|set|nos)\b", re.I)
# "273.685 KGS", "13.420 KG"
_QTY_KG = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:kgs?|kg)\b", re.I)
# an explicit "N -> RATE" / "N @ RATE" pair with an unambiguous marker.
_QTY_ARROW_RATE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:->|=>|@)\s*(\d+(?:\.\d+)?)\b")
# "N x RATE" with a bare 'x' — only a rate if RATE is fractional or >= 100
# (a size dimension like "12 x 18" is small integers and must NOT match here).
_QTY_X_RATE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+\.\d+|\d{3,})\b", re.I
)
# "... -> 425", "@ 182", "= 700" — a lone rate marker (run last)
_RATE = re.compile(r"(?:->|=>|@|=)\s*(\d+(?:\.\d+)?)\b")
_TRAILING_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*$")
# "per KGS" column marker
_PER_KG = re.compile(r"\bper\s*kgs?\b", re.I)

# strip punctuation but KEEP a '.' or 'x'/'*'/'×' that sits between digits
# (so "273.685" and "12x18" survive).
_PUNCT = re.compile(r"(?<!\d)[.,()/\\](?!\d)|[.,()/\\](?!\d)|(?<!\d)[.,()/\\]")
_WS = re.compile(r"\s+")


@dataclass
class ParsedLine:
    raw: str
    brand: str | None = None
    product: str = ""
    sku: str | None = None
    size: str | None = None
    size_kind: str | None = None  # cup | litre | nxn | no | gauge | mm | bare | None
    size_sort: float | None = None
    rate_mode: RateMode | None = None
    qty: float | None = None
    rate: float | None = None
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.7


def _norm(s: str) -> str:
    out = s.lower()
    # drop punctuation that isn't sitting between two digits
    out = re.sub(r"[.,()/\\](?!\d)|(?<!\d)[.,()/\\]", " ", out)
    return _WS.sub(" ", out).strip()


def _match_brand(text_lc: str, brands: list[str]) -> tuple[str | None, str]:
    """Return (canonical brand, text with the brand prefix removed)."""
    # Longest brand names first so "Dhara Kettle" wins over "Dhara".
    for b in sorted(brands, key=len, reverse=True):
        bl = b.lower()
        if text_lc.startswith(bl + " ") or text_lc == bl:
            return b, text_lc[len(bl):].strip()
        # brand may be a known prefix token even mid-string ("ST STORAGE BOX")
        if re.match(rf"^{re.escape(bl)}\b", text_lc):
            return b, re.sub(rf"^{re.escape(bl)}\b", "", text_lc, count=1).strip()
    return None, text_lc


def _extract_size(text: str) -> tuple[str | None, str | None, float | None, str]:
    for kind, pat in _SIZE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if kind == "nxn":
            size = f"{m.group(1)}x{m.group(2)}"
            sort = float(m.group(1))
        else:
            size = m.group(0).strip()
            sort = float(m.group(1))
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        return size, kind, sort, rest
    return None, None, None, text


def parse_product_line(
    raw: str,
    *,
    brands: list[str] | None = None,
    synonyms: dict[str, str] | None = None,
    default_rate_mode: RateMode | None = None,
) -> ParsedLine:
    brands = brands or []
    synonyms = synonyms or {}
    out = ParsedLine(raw=raw)
    if not raw or not raw.strip():
        return out

    text = _norm(raw)
    conf = 0.5  # a clean-ish line starts here; each successful extraction adds

    # token-level synonym rewrites ("kettly" -> "kettle", "s s" -> "ss")
    text = " ".join(synonyms.get(t, t) for t in text.split(" "))

    # --- rate mode column marker ---
    per_kg_col = bool(_PER_KG.search(text))
    text = _PER_KG.sub(" ", text).strip()

    # --- quantity + rate ---
    # explicit "N PC/NUG"
    if (m := _QTY_PIECE.search(text)) is not None:
        out.qty = float(m.group(1))
        out.rate_mode = RateMode.piece
        text = (text[: m.start()] + " " + text[m.end():]).strip()
        conf += 0.15
    # "N KGS" (decimal weight)
    elif (m := _QTY_KG.search(text)) is not None:
        out.qty = float(m.group(1))
        out.rate_mode = RateMode.kg
        text = (text[: m.start()] + " " + text[m.end():]).strip()
        conf += 0.15

    # "N -> RATE" / "N @ RATE" — unambiguous marker
    for pat in (_QTY_ARROW_RATE, _QTY_X_RATE):
        if out.rate is not None:
            break
        if (m := pat.search(text)) is not None:
            left = float(m.group(1))
            out.rate = float(m.group(2))
            if out.qty is None:
                out.qty = left
                if out.rate_mode is None:
                    out.rate_mode = (
                        RateMode.kg if left != int(left) else RateMode.piece
                    )
            text = (text[: m.start()] + " " + text[m.end():]).strip()
            conf += 0.15

    # a lone rate marker "-> 425" / "@ 182"
    if out.rate is None and (m := _RATE.search(text)):
        out.rate = float(m.group(1))
        text = (text[: m.start()] + " " + text[m.end():]).strip()
        conf += 0.1

    # trailing bare number, once qty is known and no rate yet ("... KGS 182")
    if out.rate is None and out.qty is not None and (m := _TRAILING_NUM.search(text)):
        out.rate = float(m.group(1))
        text = text[: m.start()].strip()
        conf += 0.05

    # --- rate-mode fallbacks ---
    if out.rate_mode is None:
        if per_kg_col:
            out.rate_mode = RateMode.kg
        elif out.qty is not None and out.qty != int(out.qty):
            out.rate_mode = RateMode.kg  # a decimal quantity is a weight
        elif out.qty is not None:
            out.rate_mode = RateMode.piece
        elif default_rate_mode is not None:
            out.rate_mode = default_rate_mode
            out.notes.append("rate_mode from group default")

    # --- brand ---
    brand, text = _match_brand(text, brands)
    if brand:
        out.brand = brand
        conf += 0.15

    # --- a leading model/article number after the brand -> sku ---
    if (m := re.match(r"^(\d{3,6})\b", text)) is not None:
        out.sku = m.group(1)
        text = text[m.end():].strip()

    # --- size ---
    size, kind, sort, text = _extract_size(text)
    if size is None and (m := _BARE_NUM.search(text)):
        size, kind, sort = m.group(1), "bare", float(m.group(1))
        text = (text[: m.start()] + " " + text[m.end():]).strip()
        out.notes.append("size is a bare number — low confidence")
        conf -= 0.1
    if size is not None:
        out.size, out.size_kind, out.size_sort = size, kind, sort
        if kind != "bare":
            conf += 0.1

    # --- whatever's left is the product phrase ---
    out.product = _WS.sub(" ", text).strip()
    if not out.brand and not out.product:
        conf -= 0.2

    out.confidence = round(max(0.0, min(1.0, conf)), 2)
    return out


def generated_name(
    *,
    category_name: str | None,
    group_name: str | None,
    sku: str | None,
    size_label: str | None,
) -> str:
    """The clean printed name for a leaf.

    With a sku:  "<category> <sku> <size>"   -> "Mintage 3499 5 Litre"
    Without:     "<group> <size>"            -> "ST Storage Box 12X18"
    """
    if sku:
        parts = [p for p in (category_name, sku, size_label) if p]
    else:
        parts = [p for p in (group_name, size_label) if p]
    return " ".join(parts).strip() or (group_name or category_name or "").strip()

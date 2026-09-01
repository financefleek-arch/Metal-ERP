"""Item-name normalization pipeline — the dedupe key behind `item.name_normalized`.

Shared by the sales catalogue-accretion path (M1) and the inward line-matcher
(ext_inward_import). Pure, no I/O: the caller loads the tenant's synonym map
once and passes it in.

Pipeline (order matters):
  1. casefold           "Monin MOJITO"        -> "monin mojito"
  2. unify separators   "1000Ml x6" / "1L-6"  -> spaces around x / punctuation
  3. strip punctuation  keep alphanumerics + spaces
  4. collapse spaces
  5. apply synonyms     token-wise, from the tenant map (longest key first)
  6. keep token order   (no sorting — "ss angle" != "angle ss" as a key would lose intent)
"""

from __future__ import annotations

import re
import unicodedata

# Split a run like "1000mlx6" or "5inx3" into "1000ml x 6" so the tokens are
# comparable across "1000Ml x6", "1000MLX6", "1000 ml * 6".
_PACK_RE = re.compile(r"(?<=[a-z0-9])\s*[x*]\s*(?=\d)")
# Everything that is not a letter, digit or space becomes a space.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def _casefold_ascii(value: str) -> str:
    # NFKD + drop combining marks: "Café" -> "cafe", full-width digits -> ascii.
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def normalize_name(name: str, synonyms: dict[str, str] | None = None) -> str:
    """Return the normalized dedupe key for an item name.

    `synonyms` is a `{from_token: to_token}` map (the tenant's `synonym` rows).
    A multi-word `from` key (e.g. "s s" -> "ss") is matched on the joined
    string before the token pass, longest first.
    """
    if not name:
        return ""

    s = _casefold_ascii(name)
    s = _PACK_RE.sub(" x ", s)
    s = _NON_ALNUM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    if not s:
        return ""

    if synonyms:
        # Phrase-level rewrites first (keys containing a space), longest key first
        # so "s s s" doesn't get half-eaten by "s s".
        phrase_keys = sorted(
            (k for k in synonyms if " " in k), key=len, reverse=True
        )
        for key in phrase_keys:
            if key in s:
                s = re.sub(rf"(?<!\S){re.escape(key)}(?!\S)", synonyms[key], s)
        # Token-level rewrites.
        tokens = [synonyms.get(tok, tok) for tok in s.split(" ")]
        s = " ".join(t for t in tokens if t)

    return s


def load_synonym_map(session: object, tenant_id: str) -> dict[str, str]:
    """Load a tenant's synonym rows into a `{from_token: to_token}` dict.

    Kept here so both call sites normalize the same way. `session` is a
    SQLAlchemy Session (typed loosely to keep this module import-light).
    """
    from sqlalchemy import select  # local import: normalize stays pure by default

    from app.models import Synonym

    result = session.execute(  # type: ignore[attr-defined]
        select(Synonym.from_token, Synonym.to_token).where(Synonym.tenant_id == tenant_id)
    )
    return {frm.casefold(): to.casefold() for frm, to in result.all()}

"""Shared LLM service — prompt templates + client + token logging.

**Config-gated stub for now.** `settings.llm_enabled` is False by default, so
`disambiguate_lines()` returns "no pick" for every line and the inward
line-matcher falls through to `low_confidence` flags instead of `llm` matches
(plan Decision 1: fuzzy-only first). X3's real implementation and X7's vision
path both build on this module without changing its call sites.

When wired for real this will hold the Anthropic client (claude-opus-5,
platform key) and write `llm_tokens` into `extraction_run`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass
class LineQuery:
    sl_no: int
    description: str
    # [(item_id, name, hsn, last_rate)] — this tenant's top candidates.
    candidates: list[tuple[str, str, str | None, float | None]]


@dataclass
class LinePick:
    sl_no: int
    item_id: str | None  # None == "NONE" (no candidate is the same product)
    confidence: float
    tokens: int = 0


def llm_enabled() -> bool:
    return bool(getattr(get_settings(), "llm_enabled", False))


def disambiguate_lines(queries: list[LineQuery]) -> list[LinePick]:
    """One batched call covering every uncertain line on a bill.

    Stub: returns a NONE pick for each. The real version sends
    description + top-5 candidates per line and returns an item id or NONE,
    capped at 0.80 confidence, `match_method = llm`.
    """
    if not llm_enabled():
        return [LinePick(q.sl_no, None, 0.0) for q in queries]

    # Real implementation lands in X3. Until then, behave as disabled even if
    # the flag is flipped, so nothing silently depends on an unbuilt path.
    raise NotImplementedError(
        "LLM disambiguation is not implemented yet (X3). Set llm_enabled=false."
    )

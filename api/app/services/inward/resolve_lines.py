"""Line resolution — per PDF line, walk the item ladder and either link a
catalogue item or stage a new UNCONFIRMED one.

Steps 1-3 come from `item_resolution.resolve_item` (exact / alias / fuzzy).
Step 4 (LLM disambiguation) is delegated to `app.services.llm` — a config-gated
stub for now, so a weak match becomes a `low_confidence` flag, not an `llm`
match. Step 5 stages a new item.

`review_flag` per line drives the review UI's red chips:
  unknown_hsn   — the line's HSN is not in `hsn_code`
  low_confidence— fuzzy < accept and no LLM pick
  ambiguous     — the LLM was consulted (only when llm_enabled)
  new           — a brand-new item is staged
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import HsnCode
from app.models._mixins import ItemSource, ItemStatus, ItemType, MatchMethod
from app.services import llm
from app.services.item_resolution import resolve_item

# UOMs that mean "discrete unit" -> item_type = mrp; else bulk.
_MRP_UOMS = {"nos", "pcs", "pc", "set", "no", "each", "unit", "box", "btl", "bottle"}


@dataclass
class LineResolution:
    sl_no: int
    match_method: MatchMethod | None = None
    match_confidence: float | None = None
    matched_item_id: str | None = None
    new_item_staged: dict[str, Any] | None = None
    review_flag: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _hsn_known(session: Session, hsn: str | None) -> bool:
    if not hsn:
        return False
    return session.scalar(select(HsnCode.code).where(HsnCode.code == hsn)) is not None


def _stage_new_item(
    session: Session,
    *,
    description: str,
    hsn: str | None,
    uom: str | None,
    synonyms: dict[str, str],
) -> tuple[dict[str, Any], str]:
    hsn_known = _hsn_known(session, hsn)
    uom_l = (uom or "").strip().lower()
    item_type = ItemType.mrp if uom_l in _MRP_UOMS else ItemType.bulk
    staged = {
        "name": description,
        "name_normalized": normalize_name(description, synonyms),
        "hsn_code": hsn if hsn_known else None,
        "uom": uom,
        "item_type": item_type.value,
        "source": ItemSource.auto_from_purchase.value,
        "status": ItemStatus.unconfirmed.value,
    }
    flag = "unknown_hsn" if (hsn and not hsn_known) else "new"
    return staged, flag


@dataclass
class _PendingLine:
    sl_no: int
    description: str
    hsn: str | None
    uom: str | None
    candidates: list[tuple[str, str, str | None, float | None]]


def resolve_lines(
    session: Session,
    tenant_id: str,
    lines: list[Any],
) -> list[LineResolution]:
    """`lines` items need `.sl_no`, `.description`, `.hsn`, `.uom` attributes
    (RawLine or the persisted InwardBillLine both fit).
    """
    synonyms = load_synonym_map(session, tenant_id)
    results: dict[int, LineResolution] = {}
    pending: list[_PendingLine] = []

    for ln in lines:
        lr = LineResolution(sl_no=ln.sl_no)
        match = resolve_item(
            session, tenant_id, ln.description, ln.hsn, synonyms=synonyms
        )
        lr.candidates = [
            {
                "item_id": c.item_id,
                "name": c.name,
                "hsn": c.hsn,
                "score": round(c.adjusted_score, 3),
            }
            for c in match.candidates
        ]

        if match.item_id and match.method in (
            MatchMethod.exact,
            MatchMethod.alias,
            MatchMethod.fuzzy,
        ):
            lr.match_method = match.method
            lr.match_confidence = match.confidence
            lr.matched_item_id = match.item_id
            results[ln.sl_no] = lr
            continue

        if match.weak and llm.llm_enabled():
            pending.append(
                _PendingLine(
                    ln.sl_no,
                    ln.description,
                    ln.hsn,
                    ln.uom,
                    [(c.item_id, c.name, c.hsn, None) for c in match.candidates[:5]],
                )
            )
            results[ln.sl_no] = lr  # filled in after the batched call
            continue

        # fuzzy-only path: weak -> low_confidence, else straight to stage-new
        staged, flag = _stage_new_item(
            session,
            description=ln.description,
            hsn=ln.hsn,
            uom=ln.uom,
            synonyms=synonyms,
        )
        lr.new_item_staged = staged
        lr.match_method = MatchMethod.new
        lr.review_flag = "low_confidence" if match.weak else flag
        results[ln.sl_no] = lr

    # --- step 4: one batched LLM call for the uncertain lines (stub: no picks) ---
    if pending:
        picks = {
            p.sl_no: pick
            for p, pick in zip(
                pending,
                llm.disambiguate_lines(
                    [llm.LineQuery(p.sl_no, p.description, p.candidates) for p in pending]
                ),
                strict=True,
            )
        }
        for p in pending:
            lr = results[p.sl_no]
            pick = picks.get(p.sl_no)
            if pick and pick.item_id:
                lr.match_method = MatchMethod.llm
                lr.match_confidence = min(pick.confidence, 0.80)
                lr.matched_item_id = pick.item_id
                lr.review_flag = "ambiguous"
            else:
                staged, flag = _stage_new_item(
                    session,
                    description=p.description,
                    hsn=p.hsn,
                    uom=p.uom,
                    synonyms=synonyms,
                )
                lr.new_item_staged = staged
                lr.match_method = MatchMethod.new
                lr.review_flag = flag if flag == "unknown_hsn" else "low_confidence"

    return [results[ln.sl_no] for ln in lines]

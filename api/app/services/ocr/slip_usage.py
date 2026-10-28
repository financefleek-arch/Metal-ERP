"""Slip-capture usage counter: record each photo sent for reading, and total them for cost.

Cost is worked out from the stored token counts at read time, using the list price of the model
that did the read. Prices are USD per million tokens (cached from Anthropic's price list 2026-10-06);
a model missing from the table is counted but costed at 0 and flagged `priced=False`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.slip_capture import SlipCapture
from app.services.ocr.extract_slip import MODEL, SlipExtraction, SlipExtractionError

# (input, output) USD per million tokens
_PRICE_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
}
_MTOK = Decimal(1_000_000)


def record_success(
    session: Session, tenant_id: str, user_id: str | None, extraction: SlipExtraction, image_bytes: int
) -> None:
    session.add(
        SlipCapture(
            tenant_id=tenant_id,
            user_id=user_id,
            model=MODEL,
            ok=True,
            line_count=len(extraction.lines),
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
            image_bytes=image_bytes,
        )
    )
    session.flush()


def record_failure(
    session: Session, tenant_id: str, user_id: str | None, exc: SlipExtractionError, image_bytes: int
) -> None:
    """A failed read is still counted (and costed when the API answered). The caller commits,
    because the request is about to raise and the session would otherwise roll this row back."""
    session.add(
        SlipCapture(
            tenant_id=tenant_id,
            user_id=user_id,
            model=MODEL,
            ok=False,
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
            image_bytes=image_bytes,
            error=str(exc)[:300],
        )
    )
    session.commit()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    price = _PRICE_PER_MTOK.get(model)
    if price is None:
        return None
    return (Decimal(input_tokens) * price[0] + Decimal(output_tokens) * price[1]) / _MTOK


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_summary(session: Session, tenant_id: str, now: datetime | None = None) -> dict:
    """This calendar month (UTC) and all time: reads, failures, tokens, estimated cost."""
    now = now or datetime.now(UTC)
    start = _month_start(now)

    def _totals(since: datetime | None) -> dict:
        stmt = (
            select(
                SlipCapture.model,
                func.count(SlipCapture.id),
                func.coalesce(func.sum(case((SlipCapture.ok.is_(False), 1), else_=0)), 0),
                func.coalesce(func.sum(SlipCapture.input_tokens), 0),
                func.coalesce(func.sum(SlipCapture.output_tokens), 0),
            )
            .where(SlipCapture.tenant_id == tenant_id)
            .group_by(SlipCapture.model)
        )
        if since is not None:
            stmt = stmt.where(SlipCapture.created_at >= since)
        reads = failed = in_tok = out_tok = 0
        cost = Decimal("0")
        priced = True
        for model, n, bad, i, o in session.execute(stmt).all():
            reads += n
            failed += int(bad)
            in_tok += int(i)
            out_tok += int(o)
            c = cost_usd(model, int(i), int(o))
            if c is None:
                priced = False
            else:
                cost += c
        return {
            "reads": reads,
            "failed": failed,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": str(cost.quantize(Decimal("0.0001"))),
            "priced": priced,
        }

    return {"month": start.strftime("%Y-%m"), "this_month": _totals(start), "all_time": _totals(None)}

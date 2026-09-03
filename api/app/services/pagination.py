"""Keyset (cursor) pagination for the big list endpoints.

Why keyset, not OFFSET: at 10k rows `OFFSET 9950` still walks 9950 rows on
every page. A keyset cursor carries the sort key of the last row seen and
turns "next page" into an indexed range scan — `WHERE (k1, k2, id) > (...)`.

Contract (backward compatible — callers that pass nothing get today's
behaviour, the whole list):

  * no `limit`  -> unbounded, no cursor, exactly as before
  * `limit=N`   -> at most N rows, plus an `X-Next-Cursor` response header
                   when more rows exist; absent header == last page
  * `cursor=…`  -> resume after that row (opaque, base64url JSON)

The cursor is signed-free on purpose: it only encodes public sort columns
already visible in the payload, and a tampered cursor can at worst skip or
repeat rows for that one request.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException, Response, status
from sqlalchemy import Select, and_, asc, desc, or_, tuple_
from sqlalchemy.sql import ColumnExpressionArgument
from sqlalchemy.sql.elements import ColumnElement

# A page size ceiling so a caller can't ask for 1e9 and pin the box.
MAX_LIMIT = 200


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[Any]:
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad)
        out = json.loads(raw)
        if not isinstance(out, list):
            raise ValueError
        return out
    except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="bad cursor"
        ) from exc


def _key_predicate(
    cols: list[ColumnExpressionArgument[Any]], directions: list[str], marker: list[Any]
) -> ColumnElement[bool]:
    """The lexicographic ``(a, b, id) > (marker)`` comparison, spelled out so
    it works with mixed asc/desc and on SQLite (no row-value comparison).

    All-ascending collapses to SQLAlchemy's native `tuple_(...) > (...)`,
    which Postgres turns straight into an index range scan.
    """
    if all(d == "asc" for d in directions):
        return tuple_(*cols) > tuple(marker)

    ors: list[ColumnElement[bool]] = []
    for i, (col, direction) in enumerate(zip(cols, directions, strict=True)):
        eq_prefix = [c == m for c, m in zip(cols[:i], marker[:i], strict=True)]
        strict = col > marker[i] if direction == "asc" else col < marker[i]
        ors.append(and_(*eq_prefix, strict))
    return or_(*ors)


def paginate(
    stmt: Select,
    *,
    order_cols: list[ColumnExpressionArgument[Any]],
    directions: list[str],
    limit: int | None,
    cursor: str | None,
) -> tuple[Select, bool]:
    """Apply ORDER BY + keyset window to `stmt`.

    `order_cols` MUST be a stable, total ordering (end it with a unique
    column, e.g. the PK). Returns `(stmt, paginated)` — when `paginated` is
    False the caller returns the rows as-is (legacy path). When True the
    caller must fetch one extra row to know whether to emit a next-cursor;
    use `finish_page`.
    """
    if len(order_cols) != len(directions):
        raise ValueError("order_cols / directions length mismatch")

    stmt = stmt.order_by(None)
    for col, direction in zip(order_cols, directions, strict=True):
        stmt = stmt.order_by(asc(col) if direction == "asc" else desc(col))

    if limit is None and cursor is None:
        return stmt, False

    if cursor is not None:
        marker = decode_cursor(cursor)
        if len(marker) != len(order_cols):
            raise HTTPException(status_code=400, detail="bad cursor")
        stmt = stmt.where(_key_predicate(order_cols, directions, marker))

    eff = MAX_LIMIT if limit is None else max(1, min(limit, MAX_LIMIT))
    stmt = stmt.limit(eff + 1)  # +1 sentinel row -> "is there a next page?"
    return stmt, True


def finish_page(
    rows: list[Any],
    *,
    limit: int | None,
    key_of: Any,
    response: Response,
) -> list[Any]:
    """Trim the sentinel row, set `X-Next-Cursor` when more rows follow.

    `key_of(row) -> list` must return the same tuple of values, in the same
    order, as the `order_cols` passed to `paginate`.
    """
    eff = MAX_LIMIT if limit is None else max(1, min(limit, MAX_LIMIT))
    has_more = len(rows) > eff
    page = rows[:eff]
    if has_more and page:
        response.headers["X-Next-Cursor"] = encode_cursor(key_of(page[-1]))
    return page

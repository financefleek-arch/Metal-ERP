"""Item-code allocation: `<PREFIX>-<6 digits>`, e.g. `GL-000412`.

One counter per (tenant, prefix), row-locked on allocation, never reused:
a code stays unique even after its item is deleted, and it is the barcode
value printed on labels, so reuse would be a real-world hazard. The unique
constraint on `supplier_catalog_item (tenant_id, code)` is the backstop.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CodeSequence, Tenant

_PREFIX_RE = re.compile(r"[A-Z0-9]{2,8}")
FALLBACK_PREFIX = "CT"


def normalize_prefix(prefix: str | None) -> str | None:
    """Upper-case and validate; None when blank or not 2-8 letters/digits."""
    if prefix is None:
        return None
    p = prefix.strip().upper()
    return p if _PREFIX_RE.fullmatch(p) else None


def derive_prefix(title: str) -> str:
    """First two letters/digits of the title ("Glassware Stock" -> "GL")."""
    alnum = re.sub(r"[^A-Za-z0-9]", "", title).upper()
    return alnum[:2] if len(alnum) >= 2 else FALLBACK_PREFIX


def resolve_prefix(tenant: Tenant, title: str, requested: str | None = None) -> str:
    """requested -> tenant default -> derived from the catalog title."""
    return (
        normalize_prefix(requested)
        or normalize_prefix(tenant.catalog_code_prefix)
        or derive_prefix(title)
    )


def format_code(prefix: str, number: int) -> str:
    return f"{prefix}-{number:06d}"


def allocate_codes(session: Session, tenant_id: str, prefix: str, count: int) -> list[str]:
    """Reserve `count` consecutive codes and return them in order.

    Locks the sequence row (FOR UPDATE on Postgres) so two uploads at once
    get disjoint ranges. Caller commits.
    """
    if count < 0:
        raise ValueError("count must be >= 0")
    p = normalize_prefix(prefix)
    if p is None:
        raise ValueError(f"invalid code prefix: {prefix!r}")
    if count == 0:
        return []

    row = _locked_row(session, tenant_id, p)
    start = row.next_value
    row.next_value = start + count
    session.flush()
    return [format_code(p, n) for n in range(start, start + count)]


def _locked_row(session: Session, tenant_id: str, prefix: str) -> CodeSequence:
    stmt = (
        select(CodeSequence)
        .where(CodeSequence.tenant_id == tenant_id, CodeSequence.prefix == prefix)
        .with_for_update()
    )
    row = session.scalar(stmt)
    if row is not None:
        return row
    # First use of this prefix. A concurrent request may insert it at the same
    # moment: the loser's insert violates the PK, so retry as a plain locked read.
    try:
        with session.begin_nested():
            session.add(CodeSequence(tenant_id=tenant_id, prefix=prefix, next_value=1))
    except IntegrityError:
        pass
    row = session.scalar(stmt)
    assert row is not None
    return row

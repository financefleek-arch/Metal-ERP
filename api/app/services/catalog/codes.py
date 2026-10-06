"""Item codes: one firm-wide running number, e.g. `100234`.

A code is a stable identifier with no meaning: it never encodes the group or the supplier, so
regrouping or renaming never touches it, and it is the barcode value printed on labels and the
part number in Tally. One counter per firm, row-locked on allocation, never reused (a code stays
unique even after its product is deleted). An optional firm prefix (`tenant.catalog_code_prefix`)
is put in front at issue time, e.g. `KS100234`; changing the prefix later affects new codes only.
The unique constraint on `catalog_product (tenant_id, code)` is the backstop.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CodeSequence, Tenant

START = 100001  # six digits from the first code, no leading zeros to lose in a spreadsheet
_COUNTER_KEY = ""  # one counter per firm; the prefix is applied at format time
_PREFIX_RE = re.compile(r"[A-Z0-9]{2,8}")


def normalize_prefix(prefix: str | None) -> str | None:
    """Upper-case and validate; None when blank or not 2-8 letters/digits."""
    if prefix is None:
        return None
    p = prefix.strip().upper()
    return p if _PREFIX_RE.fullmatch(p) else None


def firm_prefix(tenant: Tenant) -> str:
    return normalize_prefix(tenant.catalog_code_prefix) or ""


def format_code(prefix: str, number: int) -> str:
    return f"{prefix}{number}"


def allocate_codes(session: Session, tenant: Tenant, count: int) -> list[str]:
    """Reserve `count` consecutive codes and return them in order.

    Locks the counter row (FOR UPDATE on Postgres) so two uploads at once get disjoint
    ranges. Caller commits.
    """
    if count < 0:
        raise ValueError("count must be >= 0")
    if count == 0:
        return []
    row = _locked_row(session, tenant.id)
    start = row.next_value
    row.next_value = start + count
    session.flush()
    prefix = firm_prefix(tenant)
    return [format_code(prefix, n) for n in range(start, start + count)]


def _locked_row(session: Session, tenant_id: str) -> CodeSequence:
    stmt = (
        select(CodeSequence)
        .where(CodeSequence.tenant_id == tenant_id, CodeSequence.prefix == _COUNTER_KEY)
        .with_for_update()
    )
    row = session.scalar(stmt)
    if row is not None:
        return row
    # First use. A concurrent request may insert it at the same moment: the loser's insert
    # violates the PK, so retry as a plain locked read.
    try:
        with session.begin_nested():
            session.add(CodeSequence(tenant_id=tenant_id, prefix=_COUNTER_KEY, next_value=START))
    except IntegrityError:
        pass
    row = session.scalar(stmt)
    assert row is not None
    return row

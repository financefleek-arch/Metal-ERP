"""Party read-side helpers: completeness, document count, fuzzy search, dormancy.

Kept out of the router so the rules have one home and are unit-testable
without HTTP.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Party, PartyAddress
from app.schemas import PartyCompleteness

# --------------------------------------------------------------------------
# completeness — M1 rule: has an address (line1 + city + state)
# --------------------------------------------------------------------------

_ADDRESS_FIELDS = ("line1", "city", "state")


def completeness_for(party: Party) -> PartyCompleteness:
    addr = party.addresses[0] if party.addresses else None
    missing: list[str] = []
    if addr is None or not (addr.line1 and addr.line1.strip()):
        missing.append("address_line1")
    if addr is None or not (addr.city and addr.city.strip()):
        missing.append("address_city")
    if addr is None or not (addr.state_code and addr.state_code.strip()):
        missing.append("address_state")
    # Collapse to a single "address" token when nothing is filled at all.
    if len(missing) == 3:
        missing = ["address"]
    return PartyCompleteness(complete=not missing, missing=missing)


def is_incomplete(party: Party) -> bool:
    return not completeness_for(party).complete


# --------------------------------------------------------------------------
# document count — invoices + inward bills that reference this party
# Neither table references party yet, so this is 0 until those slices land.
# Written defensively: only counts a table if it exists and has a party FK.
# --------------------------------------------------------------------------


def document_count(session: Session, party_id: str) -> int:
    total = 0
    for table_name, col in (("invoice", "party_id"), ("inward_bill", "matched_party_id")):
        table = Party.metadata.tables.get(table_name)
        if table is None or col not in table.c:
            continue
        total += session.scalar(
            select(func.count()).select_from(table).where(table.c[col] == party_id)
        ) or 0
    return total


# --------------------------------------------------------------------------
# fuzzy search — trigram on legal_name (Postgres), substring on address + phone
# --------------------------------------------------------------------------

_NAME_SIMILARITY_FLOOR = 0.3

# Fuzzy search is ranked by a non-deterministic score — no stable keyset to
# page on. Cap the result; narrow with a second word.
SEARCH_RESULT_CAP = 50


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def apply_search(stmt: Select, session: Session, q: str) -> Select:
    """Widen `stmt` with an OR across name (fuzzy) / address / phone (substring),
    and order by name-similarity then recency. `stmt` must already select Party.
    """
    q = q.strip()
    if not q:
        return stmt

    like = f"%{q.lower()}%"
    is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

    # address subquery — any of this party's addresses matching line1/city
    addr_match = (
        select(PartyAddress.party_id)
        .where(
            PartyAddress.party_id == Party.id,
            or_(
                func.lower(PartyAddress.line1).like(like),
                func.lower(PartyAddress.city).like(like),
            ),
        )
        .exists()
    )

    phone_digits = _digits(q)
    conds = [func.lower(Party.legal_name).like(like), addr_match]
    if phone_digits:
        # strip non-digits from the stored phone, then substring match
        stripped = func.replace(
            func.replace(
                func.replace(
                    func.replace(
                        func.replace(func.coalesce(Party.phone, ""), "+", ""),
                        "-",
                        "",
                    ),
                    " ",
                    "",
                ),
                "(",
                "",
            ),
            ")",
            "",
        )
        conds.append(stripped.like(f"%{phone_digits}%"))

    if is_pg:
        conds.append(func.similarity(Party.legal_name, q) > _NAME_SIMILARITY_FLOOR)
        stmt = stmt.where(or_(*conds)).order_by(
            func.similarity(Party.legal_name, q).desc(),
            Party.last_txn_at.desc().nullslast(),
            func.lower(Party.legal_name),
        )
    else:
        stmt = stmt.where(or_(*conds)).order_by(
            Party.last_txn_at.desc().nullslast(), func.lower(Party.legal_name)
        )
    return stmt


# --------------------------------------------------------------------------
# dormancy
# --------------------------------------------------------------------------


def dormant_cutoff(dormant_party_days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=dormant_party_days)


def dormant_filter(cutoff: datetime) -> ColumnElement[bool]:
    """A party is dormant when it has no transaction inside the window, i.e.
    last_txn_at is older than the cutoff, OR it was never billed and was
    created before the cutoff.
    """
    return or_(
        Party.last_txn_at < cutoff,
        and_(Party.last_txn_at.is_(None), Party.created_at < cutoff),
    )

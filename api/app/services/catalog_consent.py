"""Consent to send a party price lists / catalogs on WhatsApp.

Meta treats a catalog push as a marketing message, which needs the person's opt-in. We keep the
answer on the party, with when / how / who, and every catalog send checks `may_send_catalog`.
`none` (never asked) and `opted_out` both mean do not send. An opt-out is never overwritten by a
weaker signal: only a fresh, explicit opt-in (staff, order page or a YES reply) lifts it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Party
from app.models._mixins import PartyRole, PartyStatus, WaConsent, WaConsentSource
from app.services import audit

# the three answers a customer's page and the Parties filter speak in
OFFERS_FILTERS: dict[str, WaConsent] = {
    "agreed": WaConsent.opted_in,
    "not_asked": WaConsent.none,
    "opted_out": WaConsent.opted_out,
}


def offers_counts(session: Session, tenant_id: str) -> dict[str, int]:
    """How many active customers (with a phone to send to) have agreed, not been asked, or said no."""
    rows = session.execute(
        select(Party.wa_catalog_consent, func.count())
        .where(
            Party.tenant_id == tenant_id,
            Party.status == PartyStatus.active,
            or_(Party.role == PartyRole.customer, Party.role == PartyRole.both),
            Party.phone.is_not(None),
            Party.phone != "",
        )
        .group_by(Party.wa_catalog_consent)
    ).all()
    by = {str(k): int(n) for k, n in rows}
    return {name: by.get(str(value), 0) for name, value in OFFERS_FILTERS.items()}


def may_send_catalog(party: Party) -> bool:
    return party.wa_catalog_consent == WaConsent.opted_in and bool(party.phone)


def set_catalog_consent(
    session: Session,
    party: Party,
    status: WaConsent,
    source: WaConsentSource,
    *,
    user_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record the party's answer. Returns False (and changes nothing) when it is already so."""
    if party.wa_catalog_consent == status:
        return False
    before = {"status": str(party.wa_catalog_consent)}
    party.wa_catalog_consent = status
    party.wa_catalog_consent_at = now or datetime.now(UTC)
    party.wa_catalog_consent_source = source
    party.wa_catalog_consent_by = user_id if source == WaConsentSource.manual else None
    audit.record(
        session,
        tenant_id=party.tenant_id,
        user_id=user_id,
        entity="party",
        entity_id=party.id,
        action="catalog_consent",
        before=before,
        after={"status": str(status), "source": str(source)},
    )
    session.flush()
    return True

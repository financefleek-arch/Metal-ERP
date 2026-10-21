"""Create a firm with the bootstrap every firm gets. One place, used by the operator console and by the
Fleek control plane, so a firm can never be half set up."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Tenant
from app.seed import seed_synonyms
from app.services.catalogue.seed_taxonomy import seed_taxonomy


def create_firm(
    session: Session,
    *,
    legal_name: str,
    city: str | None = None,
    fleek_org_id: str | None = None,
) -> Tenant:
    firm = Tenant(
        legal_name=legal_name.strip(),
        city=(city.strip() if city else None),
        document_label="Invoice",
        fleek_org_id=fleek_org_id,
    )
    session.add(firm)
    session.flush()

    # Normalization dictionary first (so seed_taxonomy's name_normalized keys collapse consistently), then
    # the fixed item taxonomy: the same bootstrap a self-registered firm gets.
    seed_synonyms(session, firm.id)
    session.flush()
    seed_taxonomy(session, firm.id)
    session.flush()
    return firm

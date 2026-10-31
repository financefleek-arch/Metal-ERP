"""Which Tally company a document posts to (S2.1, S2.2, S2.6, decision D-5).

Order: the company chosen on the document, then the customer's default, then the firm's default. (A
per-document-type default is a later story; nothing stores one yet.) A chosen or defaulted company must
belong to the firm and be active; a user may only choose one they can see (S1.7).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Party, TallyCompany, User
from app.services.tally.companies import get_company, get_default_company
from app.services.tally.visibility import can_see_company


def _usable(session: Session, tenant_id: str, company_id: str | None) -> TallyCompany | None:
    company = get_company(session, tenant_id, company_id) if company_id else None
    return company if company is not None and company.status == "active" else None


def choose_company(session: Session, user: User, company_id: str) -> TallyCompany:
    """The company a user picked on a document: must exist, be active and be visible to them."""
    company = _usable(session, user.tenant_id, company_id)
    if company is None or not can_see_company(user, company):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown Tally company."
        )
    return company


def resolve_company(
    session: Session, tenant_id: str, *, explicit_id: str | None = None, party: Party | None = None
) -> TallyCompany | None:
    """The company for a new document. `explicit_id` is assumed already checked by `choose_company`."""
    if explicit_id:
        company = _usable(session, tenant_id, explicit_id)
        if company is not None:
            return company
    if party is not None:
        company = _usable(session, tenant_id, party.default_company_id)
        if company is not None:
            return company
    return get_default_company(session, tenant_id)


def company_for_document(session: Session, doc) -> TallyCompany | None:  # type: ignore[no-untyped-def]
    """The company an invoice or inward bill posts to: its own, else the firm's default. A document
    whose company was since disabled still answers (the push is then blocked, not rerouted)."""
    if doc.tally_company_id:
        company = session.get(TallyCompany, doc.tally_company_id)
        if company is not None and company.tenant_id == doc.tenant_id:
            return company
    return get_default_company(session, doc.tenant_id)

"""Which Tally companies a user may see (S1.7, decision D-4).

One function decides it. Rules: the owner and a platform admin always see every company; a company with
no `visible_roles` is visible to everyone; otherwise the user's role must be in the list.

Every read of company-scoped data for a signed-in user calls this (company list and detail, sync jobs,
push status, push and check). The leak checklist, with the surface and its guard, is in
`docs/BACKLOG-tally-companion.md` under S1.7; a story that adds a read of company data adds a line.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import TallyCompany, User
from app.models._mixins import UserRole
from app.services.tally.companies import get_default_company, list_companies


def can_see_company(user: User, company: TallyCompany) -> bool:
    if user.is_platform_admin or user.role == UserRole.owner:
        return True
    roles = company.visible_roles
    if not roles:  # null or empty: everyone
        return True
    return str(user.role) in {str(r) for r in roles}


def visible_companies(
    session: Session, user: User, *, include_disabled: bool = False
) -> list[TallyCompany]:
    return [
        c
        for c in list_companies(session, user.tenant_id, include_disabled=include_disabled)
        if can_see_company(user, c)
    ]


def default_company_hidden(session: Session, user: User) -> bool:
    """The firm has a default company and this user may not see it."""
    company = get_default_company(session, user.tenant_id)
    return company is not None and not can_see_company(user, company)


def require_visible_default(session: Session, user: User) -> None:
    """404 (not 403, so it does not reveal the company exists) when the default company is hidden from
    this user. Passes when the firm has no company, so the callers' own messages still apply."""
    if default_company_hidden(session, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def require_visible_document_company(session: Session, user: User, doc) -> None:  # type: ignore[no-untyped-def]
    """404 when the company an invoice or bill posts to is hidden from this user (S1.7 + S2.1)."""
    from app.services.tally.company_routing import company_for_document

    company = company_for_document(session, doc)
    if company is not None and not can_see_company(user, company):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

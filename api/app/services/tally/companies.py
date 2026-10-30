"""A firm's Tally companies (S1.1).

A firm has one or more live Tally companies (GST books and an estimate book, a branch without its own
GSTIN, an earlier financial year). Exactly one is the firm's default; a document picks its company at
draft and falls back to the default. Every read of "the firm's company" goes through here, so nothing
else assumes there is only one.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TallyCompany, Tenant

KINDS = ("gst", "estimate", "branch", "fy", "other")
STATUSES = ("active", "disabled")


def list_companies(
    session: Session, tenant_id: str, *, include_disabled: bool = False
) -> list[TallyCompany]:
    """The firm's companies, default first."""
    q = select(TallyCompany).where(TallyCompany.tenant_id == tenant_id)
    if not include_disabled:
        q = q.where(TallyCompany.status == "active")
    return list(
        session.scalars(q.order_by(TallyCompany.is_default.desc(), TallyCompany.created_at))
    )


def get_default_company(session: Session, tenant_id: str) -> TallyCompany | None:
    """The firm's default company, or None when no Tally company is linked yet."""
    return session.scalar(
        select(TallyCompany).where(
            TallyCompany.tenant_id == tenant_id, TallyCompany.is_default.is_(True)
        )
    )


def get_company(session: Session, tenant_id: str, company_id: str) -> TallyCompany | None:
    """One of the firm's companies by id. Scoped to the firm, so another firm's id finds nothing."""
    return session.scalar(
        select(TallyCompany).where(
            TallyCompany.tenant_id == tenant_id, TallyCompany.id == company_id
        )
    )


def _default_kind(session: Session, tenant_id: str) -> str:
    tenant = session.get(Tenant, tenant_id)
    return "gst" if tenant is not None and tenant.gst_enabled else "other"


def add_company(
    session: Session,
    tenant_id: str,
    company_name: str,
    *,
    shop_id: str | None = None,
    kind: str | None = None,
) -> TallyCompany:
    """Link a Tally company to the firm. The firm's first company becomes its default; later ones are
    added beside it and never take the default by themselves."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"unknown company kind {kind!r}")
    first = get_default_company(session, tenant_id) is None
    company = TallyCompany(
        tenant_id=tenant_id,
        company_name=company_name,
        shop_id=shop_id,
        ledger_map={},
        kind=kind or _default_kind(session, tenant_id),
        is_default=first,
    )
    session.add(company)
    session.flush()
    return company


def set_default(session: Session, company: TallyCompany) -> TallyCompany:
    """Make `company` the firm's default. The old default is cleared first, so the one-default rule
    (also a partial unique index) holds at every flush."""
    if company.status != "active":
        raise ValueError("a disabled company cannot be the default")
    for other in list_companies(session, company.tenant_id, include_disabled=True):
        if other.id != company.id and other.is_default:
            other.is_default = False
    session.flush()
    company.is_default = True
    session.flush()
    return company

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
    if first:
        # Masters imported before the firm had a company kept their Tally id on the record itself.
        from app.services.tally.links import adopt_legacy_links

        adopt_legacy_links(session, company)
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


DEFAULT_SERIES = "Sales"
_KIND_SERIES = {"gst": "GST", "estimate": "EST", "branch": "BR", "fy": "FY", "other": "CO"}


def series_map(session: Session, tenant_id: str) -> dict[str, str]:
    """company id -> the invoice series that company numbers its bills in (S2.4).

    A company's own `series_prefix` wins. The default company without one keeps `Sales`, so a firm's
    existing numbers carry on unbroken. Any other company without one gets a name from its kind, made
    unique within the firm, so two companies never share a sequence.
    """
    companies = list_companies(session, tenant_id, include_disabled=True)
    out: dict[str, str] = {}
    taken: set[str] = {
        (c.series_prefix or "").strip()[:20] for c in companies if (c.series_prefix or "").strip()
    }
    taken.add(DEFAULT_SERIES)
    for c in companies:
        prefix = (c.series_prefix or "").strip()[:20]
        if prefix:
            out[c.id] = prefix
        elif c.is_default:
            out[c.id] = DEFAULT_SERIES
    for c in companies:
        if c.id in out:
            continue
        base = _KIND_SERIES.get(c.kind, "CO")
        name, n = base, 1
        while name in taken:
            n += 1
            name = f"{base}{n}"
        taken.add(name)
        out[c.id] = name
    return out


def series_for(session: Session, company: TallyCompany | None) -> str:
    """The invoice series for `company` (`Sales` when the firm has none)."""
    if company is None:
        return DEFAULT_SERIES
    return series_map(session, company.tenant_id).get(company.id, DEFAULT_SERIES)


class CompanyError(ValueError):
    """A company change that cannot be made; the message is shown to the owner as is."""


ROLE_CHOICES = ("accountant", "viewer", "counter", "weighbridge", "rate_desk")


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def check_unique(
    session: Session,
    tenant_id: str,
    *,
    name: str | None = None,
    prefix: str | None = None,
    except_id: str | None = None,
) -> None:
    """A firm's companies differ in name and in bill-number prefix (S1.4). Raises `CompanyError`."""
    for c in list_companies(session, tenant_id, include_disabled=True):
        if c.id == except_id:
            continue
        if name and _same(c.company_name, name):
            raise CompanyError(f"You already have a company called {c.company_name}.")
        if prefix and (c.series_prefix or "").strip().lower() == prefix.strip().lower():
            raise CompanyError(f"{c.company_name} already numbers its bills {c.series_prefix}.")
    if prefix and prefix.strip().lower() == DEFAULT_SERIES.lower():
        default = get_default_company(session, tenant_id)
        if default is not None and default.id != except_id and not (default.series_prefix or "").strip():
            raise CompanyError(f"{default.company_name} already numbers its bills {DEFAULT_SERIES}.")


def update_company(session: Session, company: TallyCompany, patch: dict) -> TallyCompany:
    """Apply an owner's edit of the business-side fields. A new name forgets the stored GUID and
    verdict (the owner pointed the link at another company on purpose)."""
    if "company_name" in patch:
        name = (patch["company_name"] or "").strip()
        if not name:
            raise CompanyError("The company needs a name.")
        check_unique(session, company.tenant_id, name=name, except_id=company.id)
        if not _same(name, company.company_name):
            company.company_guid = None
            company.books_from = None
            company.verify_state = None
            company.verify_note = None
            company.verified_at = None
        company.company_name = name
    if "kind" in patch:
        if patch["kind"] not in KINDS:
            raise CompanyError("Unknown company kind.")
        company.kind = patch["kind"]
    if "document_label" in patch:
        company.document_label = (patch["document_label"] or "").strip()[:40] or None
    if "series_prefix" in patch:
        prefix = (patch["series_prefix"] or "").strip()[:20]
        if prefix:
            check_unique(session, company.tenant_id, prefix=prefix, except_id=company.id)
        company.series_prefix = prefix or None
    if "include_in_reminders" in patch:
        company.include_in_reminders = bool(patch["include_in_reminders"])
    if "auto_refresh" in patch:
        company.auto_refresh = bool(patch["auto_refresh"])
    if "visible_roles" in patch:
        roles = patch["visible_roles"]
        if roles is not None:
            bad = [r for r in roles if r not in ROLE_CHOICES]
            if bad:
                raise CompanyError("Unknown role: " + ", ".join(bad))
            roles = sorted(set(roles))
        company.visible_roles = roles or None
    session.flush()
    return company


def set_active(session: Session, company: TallyCompany, active: bool) -> TallyCompany:
    """Switch a company off or on. Nothing is deleted. The default stays on until another is."""
    if not active:
        if company.is_default:
            raise CompanyError("Make another company the default before switching this one off.")
        company.status = "disabled"
    else:
        company.status = "active"
    session.flush()
    return company

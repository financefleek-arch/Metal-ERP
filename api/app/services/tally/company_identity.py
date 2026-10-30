"""Which company a job is for, as the agent needs to see it (S0.3).

Every Tally job names its company. The agent compares that with what the gateway has open before it
sends anything, by GUID when we know it (a rename or a same-named other company is then caught) and by
name otherwise. An older agent ignores `company_guid` and keeps working by name.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import BackupShop, TallyCompany, TallySyncJob

# What the agent reports when it stops a job because of the company. Nothing was sent to Tally, so none
# of these leaves an unknown outcome (S0.6).
HARD_STOPS = ("wrong_company", "company_renamed", "company_ambiguous")

# What the agent pings while it keeps a job and waits for the company to be opened.
WAITING_FOR_COMPANY = "company_not_open"


def not_open_message(session: Session, job: TallySyncJob) -> str:
    """Why a job that waited for its company was cancelled: names the company and what IS open (from
    the agent's last report) so the owner can open the right one or fix the name."""
    company = session.get(TallyCompany, job.company_id)
    ours = company.company_name if company else "The linked company"
    shop = session.get(BackupShop, company.shop_id) if company and company.shop_id else None
    names = [c.get("name", "") for c in (shop.tally_companies or [])] if shop else []
    listed = ", ".join('"' + n + '"' for n in names)
    now_open = f" Open now: {listed}." if names else ""
    return (
        f'"{ours}" is not open in TallyPrime, so this was cancelled.{now_open} '
        "Open it in TallyPrime (or correct the company name here), then send again."
    )


def company_payload(company: TallyCompany) -> dict[str, str]:
    out = {"company_name": company.company_name}
    if company.company_guid:
        out["company_guid"] = company.company_guid
    return out

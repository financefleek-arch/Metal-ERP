"""What a firm's plan lets it use (docs/PLAN-onboarding-and-account.md section 3.5, docs/PLAN-shop-activity-intelligence.md section 8).

One answer to "may this firm do X", in one place. Fleek owns the plans: each plan lists the feature keys it switches on, and that list rides in the
licence Fleek reports (`licence.features`). So Free, Starter, Growth and Complete are not special-cased here; a Fleek admin edits what a plan unlocks
in Plan templates and the next licence refresh applies it.

* A firm **not linked to Fleek**, and a firm on an **older plan** whose licence carries no list, keep everything (as before this existed).
* A firm on the **Free plan** has nothing switched on, except `pdf_out` (downloading and sharing invoice PDFs) for the first 7 days of the account
  ("Free is Starter for 7 days"). For a firm that was already on Free when the ladder went live, the 7 days run from `LADDER_LIVE_AT`.
* A firm that signed itself up and whose paid plan has **lapsed** (a trial that ended without payment, a cancelled plan, unpaid beyond Fleek's
  grace) is held to the Free rules. A firm Fleek or the operator made keeps the lenient rule: it is only warned.

The module-level switches that already exist (`ext_inward_import`, `ext_supplier_catalog`, the WhatsApp connection) are unchanged and still apply
on top; they are off by default for a new firm.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.deps import SessionDep, get_current_user
from app.models import Tenant
from app.services import entitlements
from app.services.entitlements import Licence

FREE_PLAN = "metal_erp_free"
FREE_PDF_DAYS = 7
# When the plan ladder went live. A shop already on Free then gets its 7 days from here, not from its sign-up date.
LADDER_LIVE_AT = datetime(2026, 11, 1, tzinfo=UTC)


class _Unset:
    """Marks "not looked up yet" (None already means a firm not linked to Fleek)."""


_UNSET = _Unset()

# key -> what the person sees in "not on your plan" and on the Setup cards
FEATURES: dict[str, str] = {
    "pdf_out": "Downloading and sharing invoice PDFs",
    "whatsapp": "Sending from your own WhatsApp number",
    "orders": "Customer ordering and order tracking",
    "collections": "The collections (ageing) view",
    "reminders": "Payment reminders",
    "catalog_parse": "Customer catalog parsing",
    "inward_prices": "Inward price-list import",
    "import": "Importing parties and items from Excel or Tally",
    "slip": "Creating an invoice from a photo of a slip",
    "voice": "Creating an invoice by speaking",
    "tally_backup": "Tally backup to the cloud",
    "tally_full": "The full Tally integration",
}
# Older clients ask for one `tally` key; it means the backup connection.
ALIASES: dict[str, str] = {"tally": "tally_backup"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _lookup(session: Session, tenant: Tenant, licence: Licence | None | _Unset) -> Licence | None:
    return entitlements.current(session, tenant) if isinstance(licence, _Unset) else licence


def is_free(session: Session, tenant: Tenant, licence: Licence | None | _Unset = _UNSET) -> bool:
    """Is the firm held to the Free rules. Pass `licence` when the caller already has it, to save a lookup."""
    licence = _lookup(session, tenant, licence)
    if licence is None:
        return False  # not linked to Fleek: unlimited, as before
    if licence.plan_key is not None:
        if licence.plan_key == FREE_PLAN:
            return True
        # a plan that has lapsed holds only a firm that signed itself up; the others keep the lenient rule
        return licence.lapsed and tenant.signup_source == "self"
    # Fleek has not answered and never has: a firm that signed up in the app started on Free
    return tenant.signup_source == "self"


def pdf_window_ends(tenant: Tenant) -> datetime:
    return max(_aware(tenant.created_at), LADDER_LIVE_AT) + timedelta(days=FREE_PDF_DAYS)


def pdf_days_left(tenant: Tenant, now: datetime | None = None) -> int:
    """Whole days of Free PDF access left (0 once it has ended)."""
    left = (pdf_window_ends(tenant) - (now or datetime.now(UTC))).total_seconds()
    return math.ceil(left / 86400) if left > 0 else 0


def allowed(
    session: Session, tenant: Tenant, key: str, licence: Licence | None | _Unset = _UNSET, now: datetime | None = None
) -> bool:
    key = ALIASES.get(key, key)
    if key not in FEATURES:
        return True
    licence = _lookup(session, tenant, licence)
    if licence is None:
        return True
    if is_free(session, tenant, licence):
        return key == "pdf_out" and (now or datetime.now(UTC)) < pdf_window_ends(tenant)
    if licence.features is None:
        return True  # an older licence with no list: everything, as before
    return key in licence.features


def snapshot(
    session: Session, tenant: Tenant, licence: Licence | None | _Unset = _UNSET, now: datetime | None = None
) -> dict[str, bool]:
    licence = _lookup(session, tenant, licence)
    out = {key: allowed(session, tenant, key, licence, now) for key in FEATURES}
    for alias, key in ALIASES.items():
        out[alias] = out[key]
    return out


def unlocking_plan(key: str, plans: list[dict]) -> dict | None:
    """The cheapest plan in Fleek's ladder that switches `key` on (so a locked screen can say what it costs), or None."""
    key = ALIASES.get(key, key)
    paid = [p for p in plans if p.get("price_amount", 0) > 0 and key in (p.get("unlocks") or [])]
    return min(paid, key=lambda p: p["price_amount"]) if paid else None


_optional_bearer = HTTPBearer(auto_error=False)


def require(key: str):  # type: ignore[no-untyped-def]
    """Route dependency: 403 `not_on_plan` when the firm's plan does not include `key`.

    A request with no bearer header passes this check and meets the route's own authentication. That is how a photo or a PDF opened by an
    `<img>` or `<iframe>` (which cannot send a header) reaches its signed-URL route; the call that hands out such a URL is itself gated."""
    key = ALIASES.get(key, key)

    def dep(
        request: Request,
        session: SessionDep,
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
    ) -> None:
        if creds is None:
            return
        user = get_current_user(creds, session, request)
        tenant = session.get(Tenant, user.tenant_id)
        if tenant is not None and not allowed(session, tenant, key):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "not_on_plan",
                    "feature": key,
                    "message": f"{FEATURES[key]} is not part of your plan. Open Plan and billing to see the plans.",
                },
            )

    return Depends(dep)

"""What a firm's plan lets it use (docs/PLAN-onboarding-and-account.md section 3.5).

One answer to "may this firm do X", in one place. A firm on the Free plan gets invoicing, parties,
items and payments; the features below are off for it until Fleek moves it to a paid plan. Every
other firm (made by Fleek, the operator, or live before this existed) keeps everything: only a firm
whose plan IS the Free plan is held back.

The module-level switches that already exist (`ext_inward_import`, `ext_supplier_catalog`, the
licence, the WhatsApp connection) are unchanged and still apply on top; they are off by default for
a new firm.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import CurrentUser, SessionDep
from app.models import Tenant
from app.services import entitlements
from app.services.entitlements import Licence

FREE_PLAN = "metal_erp_free"


class _Unset:
    """Marks "not looked up yet" (None already means a firm not linked to Fleek)."""


_UNSET = _Unset()

# key -> what the person sees in "not on your plan" and on the Setup cards
FEATURES: dict[str, str] = {
    "import": "Importing parties and items from Excel or Tally",
    "slip": "Creating an invoice from a photo of a slip",
    "voice": "Creating an invoice by speaking",
    "collections": "The collections (ageing) view",
    "reminders": "Payment reminders",
    "whatsapp": "Sending from your own WhatsApp number",
    "tally": "The Tally connection",
}


def is_free(session: Session, tenant: Tenant, licence: Licence | None | _Unset = _UNSET) -> bool:
    """Pass `licence` when the caller already has it, to save a lookup."""
    if isinstance(licence, _Unset):
        licence = entitlements.current(session, tenant)
    if licence is None:
        return False  # not linked to Fleek: unlimited, as before
    if licence.plan_key is not None:
        return licence.plan_key == FREE_PLAN
    # Fleek has not answered and never has: a firm that signed up in the app started on Free
    return tenant.signup_source == "self"


def allowed(session: Session, tenant: Tenant, key: str) -> bool:
    return key not in FEATURES or not is_free(session, tenant)


def snapshot(session: Session, tenant: Tenant, licence: Licence | None | _Unset = _UNSET) -> dict[str, bool]:
    free = is_free(session, tenant, licence)
    return {key: not free for key in FEATURES}


def require(key: str):  # type: ignore[no-untyped-def]
    """Route dependency: 403 `not_on_plan` when the firm's plan does not include `key`."""

    def dep(user: CurrentUser, session: SessionDep) -> None:
        tenant = session.get(Tenant, user.tenant_id)
        if tenant is not None and not allowed(session, tenant, key):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "not_on_plan",
                    "feature": key,
                    "message": f"{FEATURES[key]} is not part of the Free plan. "
                    "Open Setup and ask Fleek to switch it on.",
                },
            )

    return Depends(dep)

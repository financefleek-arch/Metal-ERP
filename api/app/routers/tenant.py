"""The firm (tenant) profile — read and update.

One tenant per user; there is no create route here because a tenant is
born with the owner at /api/auth/register. `PATCH /api/tenant` fills in
the rest (address, bank block, document label, ...) during onboarding.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Tenant
from app.schemas import TenantOut, TenantUpdate

router = APIRouter(prefix="/api/tenant", tags=["tenant"])


def _load(session: SessionDep, tenant_id: str) -> Tenant:
    # The FK guarantees it exists; get() is a PK lookup.
    return session.get(Tenant, tenant_id)  # type: ignore[return-value]


@router.get("", response_model=TenantOut)
def get_tenant(user: CurrentUser, session: SessionDep) -> Tenant:
    return _load(session, user.tenant_id)


@router.patch("", response_model=TenantOut)
def update_tenant(
    body: TenantUpdate, user: WriteUser, session: SessionDep
) -> Tenant:
    tenant = _load(session, user.tenant_id)
    if body.reminder_auto_send and not tenant.reminder_auto_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Automatic reminders are not switched on for your firm. Please contact Fleek.",
        )
    not_nullable = {
        "reminder_enabled",
        "reminder_auto_send",
        "reminder_days",
        "default_credit_days",
    }
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field in not_nullable:
            continue  # an explicit null means "leave it", not "erase it"
        setattr(tenant, field, value)
    session.flush()
    return tenant

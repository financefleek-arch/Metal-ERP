"""The firm (tenant) profile — read and update.

One tenant per user; there is no create route here because a tenant is
born with the owner at /api/auth/register. `PATCH /api/tenant` fills in
the rest (address, bank block, document label, ...) during onboarding.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUser, SessionDep, WriteUser
from app.models import Tenant
from app.schemas import BillPreviewOut, BillPreviewRequest, TenantOut, TenantUpdate
from app.services import activity, onboarding, upi_qr
from app.services.invoices.pdf import render_bill_preview_html

router = APIRouter(prefix="/api/tenant", tags=["tenant"])


def _load(session: SessionDep, tenant_id: str) -> Tenant:
    # The FK guarantees it exists; get() is a PK lookup.
    return session.get(Tenant, tenant_id)  # type: ignore[return-value]


@router.get("", response_model=TenantOut)
def get_tenant(user: CurrentUser, session: SessionDep) -> Tenant:
    return _load(session, user.tenant_id)


@router.post("/bill-preview", response_model=BillPreviewOut)
def bill_preview(body: BillPreviewRequest, user: CurrentUser, session: SessionDep) -> BillPreviewOut:
    """The bill as it would print, from the details on screen, for the Firm page's preview."""
    edits = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "document_label" or v}
    return BillPreviewOut(html=render_bill_preview_html(_load(session, user.tenant_id), edits))


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
        "gst_enabled",
    }
    had_details = onboarding.firm_details_done(tenant)
    data = body.model_dump(exclude_unset=True)
    gstin = data.get("gstin", tenant.gstin)
    if data.get("gst_enabled") and not gstin:
        raise HTTPException(
            status_code=422,
            detail="Enter your GSTIN to switch on GST invoices.",
        )
    if data.get("gstin"):
        # The GSTIN already says which state and PAN it belongs to; never ask for them twice.
        data["state_code"] = data["gstin"][:2]
        data["pan"] = data["gstin"][2:12]
    for field, value in data.items():
        if value is None and field in not_nullable:
            continue  # an explicit null means "leave it", not "erase it"
        setattr(tenant, field, value)
    if tenant.upi_qr_payload and not tenant.upi_id:
        tenant.upi_id = upi_qr.payee_of(tenant.upi_qr_payload)  # the QR already says who is paid
    if tenant.gst_enabled and not tenant.gstin:
        tenant.gst_enabled = False  # clearing the GSTIN ends GST mode
    if not had_details and onboarding.firm_details_done(tenant):
        activity.record(session, tenant_id=user.tenant_id, user_id=user.id, key="firm_details_saved")
    session.flush()
    return tenant

"""The shop owner's WhatsApp page, mirrored 1:1 from Fleek's advisor page (docs/PARITY-whatsapp-connect.md).

Same four routes as Fleek's /api/advisor/whatsapp-integrations, same request bodies, same answers (Fleek's own
bodies and status codes pass through). Owner only: Fleek lets only an org admin disconnect, and the number is
the firm's identity with its customers. A firm not yet linked to a Fleek organisation cannot connect (an
operator links it first). After each call the cached number is rewritten from what Fleek reports.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import CurrentUser, SessionDep
from app.models import Tenant, User
from app.models._mixins import UserRole
from app.services import audit, features
from app.services import whatsapp_connect as svc

router = APIRouter(
    prefix="/api/whatsapp-integrations", tags=["whatsapp"], dependencies=[features.require("whatsapp")]
)


def require_owner(user: CurrentUser) -> User:
    if user.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can manage WhatsApp")
    return user


Owner = Annotated[User, Depends(require_owner)]


def _org_id(session: Session, user: User) -> str:
    firm = session.get(Tenant, user.tenant_id)
    if firm is None or not firm.fleek_org_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This firm is not linked to Fleek yet. Ask Fleek to link it first."
        )
    return firm.fleek_org_id


def _ask(
    method: str, suffix: str, owner: User, session: Session, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """Call Fleek, then refresh the cached number from Fleek's state. -> Fleek's (status, body)."""
    org_id = _org_id(session, owner)
    try:
        code, body = svc.call(method, org_id, suffix, payload)
        if code < 300:
            state = body
            if suffix:  # a change went through: read the new state
                _, state = svc.call("GET", org_id)
            svc.sync_cache(session, owner.tenant_id, state)
    except svc.FleekUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return code, body


def _out(code: int, body: dict[str, Any]) -> JSONResponse:
    if code in (401, 403):  # Fleek refusing THIS server's call is not the owner's login failing
        return JSONResponse(
            {"error": "fleek_unavailable", "message": "WhatsApp is not available right now."},
            status_code=502,
        )
    # the number's ids are for this app's server, not for the page
    return JSONResponse({k: v for k, v in body.items() if k != "connection"}, status_code=code)


def _audited(session: Session, owner: User, action: str) -> None:
    audit.record(
        session,
        tenant_id=owner.tenant_id,
        user_id=owner.id,
        entity="whatsapp_integration",
        entity_id=owner.tenant_id,
        action=action,
    )


@router.get("")
def get_status(owner: Owner, session: SessionDep) -> JSONResponse:
    return _out(*_ask("GET", "", owner, session))


@router.post("/connect")
def connect(owner: Owner, session: SessionDep, payload: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    code, body = _ask("POST", "/connect", owner, session, payload)
    if code < 300:
        _audited(session, owner, "connect")
    return _out(code, body)


@router.post("/retry-registration")
def retry_registration(
    owner: Owner, session: SessionDep, payload: Annotated[dict[str, Any] | None, Body()] = None
) -> JSONResponse:
    return _out(*_ask("POST", "/retry-registration", owner, session, payload or {}))


@router.post("/disconnect")
def disconnect(owner: Owner, session: SessionDep) -> JSONResponse:
    code, body = _ask("POST", "/disconnect", owner, session, {})
    if code < 300:
        _audited(session, owner, "disconnect")
    return _out(code, body)

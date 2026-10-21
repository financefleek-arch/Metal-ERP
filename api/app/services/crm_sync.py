"""Pushing a shop's customers to the Fleek CRM, and taking consent back (control plane plan section 15).

Fleek's CRM runs the shop's campaigns; this app keeps the customers. Each party with a phone number is
pushed with:
  * its Metal ERP id (`external_id`), name and phone;
  * its personal catalog link token (a capability: whoever holds it is this customer; minted here and
    reused for every campaign);
  * what we know about WhatsApp marketing consent (status, how, when). There is no criterion on who is
    pushed (owner decision 2026-10-21): the CRM's own gate decides who may be messaged, so a customer
    with no consent is simply never sent to.
Consent travels both ways and an opt-out always wins: a STOP in the CRM comes back here
(`apply_opt_out`), and when the CRM reports it already holds a refusal for someone we thought had opted
in, we record the opt-out too.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Party, Tenant
from app.models._mixins import PartyStatus, WaConsent, WaConsentSource
from app.services import catalog_consent
from app.services.control_plane_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

log = logging.getLogger(__name__)

BATCH = 500
TIMEOUT_SECONDS = 30.0

# our consent source -> the word the CRM records
_SOURCE_TO_CRM = {
    WaConsentSource.manual.value: "manual",
    WaConsentSource.order_page.value: "order_page",
    WaConsentSource.whatsapp_reply.value: "whatsapp_reply",
}


class SyncError(Exception):
    """The push could not be made (not linked, not configured, Fleek refused or unreachable)."""


@dataclass
class SyncResult:
    sent: int = 0
    status: dict[str, int] = field(default_factory=dict)
    consent: dict[str, int] = field(default_factory=dict)
    opted_out_here: int = 0


def new_token() -> str:
    return secrets.token_urlsafe(9)  # 12 url-safe characters


def ensure_link_token(session: Session, party: Party) -> str:
    if not party.link_token:
        party.link_token = new_token()
        session.flush()
    return party.link_token


def consent_payload(party: Party) -> dict[str, Any]:
    """Pure. What we tell the CRM about this party's consent."""
    status = str(party.wa_catalog_consent or WaConsent.none)
    if status == WaConsent.none.value:
        return {"status": "none"}
    source = _SOURCE_TO_CRM.get(str(party.wa_catalog_consent_source or ""), "manual")
    if status == WaConsent.opted_out.value and source == "whatsapp_reply":
        source = "customer_stop"
    at = party.wa_catalog_consent_at
    return {"status": status, "source": source, "at": at.isoformat() if at else None}


def item_for(party: Party, token: str) -> dict[str, Any]:
    """Pure."""
    return {
        "external_id": party.id,
        "name": party.legal_name,
        "phone": party.phone,
        "link_token": token,
        "consent": consent_payload(party),
    }


def parties_to_push(session: Session, tenant_id: str) -> list[Party]:
    return list(
        session.scalars(
            select(Party)
            .where(
                Party.tenant_id == tenant_id,
                Party.status == PartyStatus.active,
                Party.phone.is_not(None),
                Party.phone != "",
            )
            .order_by(Party.created_at, Party.id)
        )
    )


def batches(items: list[Any], size: int = BATCH) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _put(
    fleek_org_id: str, items: list[dict[str, Any]], client: httpx.Client | None
) -> dict[str, Any]:
    s = get_settings()
    if not s.control_plane_url or not s.control_plane_secret:
        raise SyncError("the control plane is not configured")
    path = f"/api/internal/metal-erp/orgs/{fleek_org_id}/contacts"
    body = json.dumps({"contacts": items}, separators=(",", ":")).encode()
    stamp = str(int(datetime.now(UTC).timestamp()))
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: sign(
            s.control_plane_secret, timestamp=stamp, method="PUT", path=path, body=body
        ),
    }
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        resp = http.put(s.control_plane_url.rstrip("/") + path, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise SyncError(f"could not reach the control plane: {exc.__class__.__name__}") from exc
    finally:
        if own:
            http.close()
    if resp.status_code != 200:
        raise SyncError(f"the control plane answered {resp.status_code}")
    try:
        out = resp.json()
    except ValueError as exc:
        raise SyncError("the control plane sent an unreadable answer") from exc
    if not isinstance(out, dict) or not isinstance(out.get("results"), list):
        raise SyncError("the control plane sent an unexpected answer")
    return out


def push(
    session: Session,
    tenant: Tenant,
    *,
    client: httpx.Client | None = None,
    sender: Callable[[str, list[dict[str, Any]], httpx.Client | None], dict[str, Any]] = _put,
) -> SyncResult:
    """Push every active party with a phone, in batches. Idempotent. Raises SyncError. A consent the CRM
    refused to lift (it holds an opt-out)
    is recorded here as an opt-out, so the two systems agree."""
    if not tenant.fleek_org_id:
        raise SyncError("this firm is not linked to Fleek")
    if sender is _put and not (get_settings().control_plane_url and get_settings().control_plane_secret):
        raise SyncError("the control plane is not configured")  # said even when there is nothing to push yet
    parties = parties_to_push(session, tenant.id)
    by_id = {p.id: p for p in parties}
    out = SyncResult()
    for chunk in batches(parties):
        items = [item_for(p, ensure_link_token(session, p)) for p in chunk]
        answer = sender(tenant.fleek_org_id, items, client)
        for r in answer["results"]:
            out.status[str(r.get("status"))] = out.status.get(str(r.get("status")), 0) + 1
            out.consent[str(r.get("consent"))] = out.consent.get(str(r.get("consent")), 0) + 1
            if r.get("consent") == "blocked_by_refusal":
                party = by_id.get(str(r.get("external_id")))
                if party is not None and catalog_consent.set_catalog_consent(
                    session, party, WaConsent.opted_out, WaConsentSource.whatsapp_reply
                ):
                    out.opted_out_here += 1
        out.sent += len(items)
    session.flush()
    return out


def apply_opt_out(
    session: Session, tenant_id: str, external_id: str, *, at: datetime | None = None
) -> bool:
    """The CRM reports that this customer opted out (a STOP, a stop inside WhatsApp, a complaint). Only ever
    an opt-out: nothing here can opt a customer in.
    Returns True when the party's consent changed."""
    party = session.get(Party, external_id)
    if party is None or party.tenant_id != tenant_id:
        return False
    return catalog_consent.set_catalog_consent(
        session, party, WaConsent.opted_out, WaConsentSource.whatsapp_reply, now=at
    )


def party_by_token(session: Session, tenant_id: str, token: str | None) -> Party | None:
    """The customer behind a personal link token, for this firm only (a token from another firm is nobody)."""
    if not token or len(token) > 40:
        return None
    party = session.scalar(select(Party).where(Party.link_token == token))
    if party is None or party.tenant_id != tenant_id or party.status != PartyStatus.active:
        return None
    return party

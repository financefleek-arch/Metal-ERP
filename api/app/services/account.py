"""Sign-up and password reset (docs/PLAN-onboarding-and-account.md).

Sign-up: a pending `signup_request` holds what the person typed. One WhatsApp code to the mobile proves
they control it; the mobile is the sign-in, and the email is optional and never verified. Only then is the
Fleek organisation made (Free plan), and only then the firm and its owner here. Fleek is asked first: if it
cannot answer, no firm is made, so there is never a firm outside the control plane.

Reset: a WhatsApp code to the mobile (or, for an older account that has no mobile, a code to its email) lets
the person choose a new password; every older session ends.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import SignupRequest, Tenant, User
from app.models._mixins import UserRole
from app.reference import validate_phone
from app.security import hash_password
from app.services import activity, auth_codes, auth_throttle, firm_provisioning, passwords
from app.services.auth_codes import CodeError
from app.services.fleek_signed import FleekUnavailable
from app.services.fleek_signed import request as fleek_request

log = logging.getLogger(__name__)

TERMS_VERSION = "2026-10"
SIGNUP_TTL = timedelta(minutes=30)
_INDIAN_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")
CLASH = "An account with this mobile number already exists. Sign in, or reset your password."
FLEEK_DOWN = "We could not set up your account just now. Please try again in a minute."
CODES_PATH = "/api/internal/metal-erp/codes"
SELF_SIGNUP_PATH = "/api/internal/metal-erp/self-signup"


def normalize_mobile(raw: str) -> str:
    """+91XXXXXXXXXX, or ValueError with the sentence to show."""
    try:
        value = validate_phone(raw)
    except ValueError:
        value = None
    if not value or not _INDIAN_MOBILE.match(value):
        raise ValueError("Enter a valid 10-digit Indian mobile number starting with 6 to 9")
    return value


def mask_mobile(m: str) -> str:
    return f"+91 {m[3:5]}{'•' * 6}{m[-2:]}"


def find_user(session: Session, identifier: str) -> User | None:
    """The person a sign-in or reset names: a mobile number, or the email of an older account."""
    ident = (identifier or "").strip()
    if "@" in ident:
        cond = func.lower(User.email) == ident.lower()
    else:
        try:
            cond = User.mobile == normalize_mobile(ident)
        except ValueError:
            return None
    # the schema allows a stray duplicate email in two firms; never let that turn a sign-in into a 500
    return session.scalars(select(User).where(cond).order_by(User.created_at)).first()


def identity_key(identifier: str) -> str:
    """One spelling per person for the throttles, so "98765 43210" and "+919876543210" share a count."""
    ident = (identifier or "").strip()
    if "@" in ident:
        return ident.lower()
    try:
        return normalize_mobile(ident)
    except ValueError:
        return ident.lower()[:200]


# --------------------------------------------------------------------------- carrying a code (via Fleek)


def _carry(path: str, payload: dict[str, str]) -> None:
    try:
        status, body = fleek_request("POST", path, payload)
    except FleekUnavailable:
        raise CodeError("We could not send the code just now. Try again in a minute.", status=503) from None
    if status == 429:
        raise CodeError("Too many codes were requested. Try again in an hour.", status=429)
    if status != 200:
        raise CodeError("We could not send the code. Check the number or email and try again.", status=502)


def deliver_whatsapp(to: str, code: str) -> None:
    _carry(f"{CODES_PATH}/whatsapp", {"to": to, "code": code})


def make_email_deliverer(purpose: str):  # type: ignore[no-untyped-def]
    def deliver(to: str, code: str) -> None:
        _carry(f"{CODES_PATH}/email", {"to": to, "code": code, "purpose": purpose})

    return deliver


# --------------------------------------------------------------------------------------- sign-up


@dataclass(frozen=True)
class SignupStarted:
    request_id: str
    mobile_masked: str


def _taken(session: Session, mobile: str) -> bool:
    # the mobile is the sign-in, so it alone must be unique; an email is only contact detail
    return session.scalar(select(User.id).where(User.mobile == mobile)) is not None


def start_signup(
    session: Session,
    *,
    firm_name: str,
    owner_name: str,
    email: str | None,
    mobile: str,
    password: str,
    ip: str | None,
) -> SignupStarted:
    email = (email or "").lower().strip() or None
    mobile = normalize_mobile(mobile)
    bad = passwords.problem(password, email=email, firm=firm_name, mobile=mobile)
    if bad:
        raise CodeError(bad, status=422)
    if not auth_throttle.hit(session, "signup_ip", ip or "unknown", limit=10, window_seconds=3600):
        raise CodeError("Too many sign-up attempts. Try again later.", status=429)
    if _taken(session, mobile):
        raise CodeError(CLASH, status=409)

    now = datetime.now(UTC)
    # only the latest attempt for this mobile stays open
    session.execute(
        update(SignupRequest)
        .where(SignupRequest.status == "pending", SignupRequest.mobile == mobile)
        .values(status="cancelled")
    )
    req = SignupRequest(
        firm_name=firm_name.strip(),
        owner_name=owner_name.strip(),
        email=email,
        mobile=mobile,
        password_hash=hash_password(password),
        terms_version=TERMS_VERSION,
        terms_accepted_at=now,
        status="pending",
        expires_at=now + SIGNUP_TTL,
        client_ip=ip,
    )
    session.add(req)
    session.commit()
    send_signup_code(session, req)
    return SignupStarted(req.id, mask_mobile(mobile))


def _open_request(session: Session, request_id: str) -> SignupRequest:
    req = session.get(SignupRequest, request_id)
    if req is None or req.status == "cancelled":
        raise CodeError("This sign-up has expired. Please start again.", status=410)
    if req.status == "done":
        raise CodeError("This account is already created. Sign in.", status=409)
    expires = req.expires_at if req.expires_at.tzinfo else req.expires_at.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        raise CodeError("This sign-up has expired. Please start again.", status=410)
    return req


def send_signup_code(session: Session, req: SignupRequest) -> None:
    auth_codes.issue(
        session,
        purpose="signup",
        subject_id=req.id,
        channel="whatsapp",
        target=req.mobile,
        deliver=deliver_whatsapp,
    )


def resend_signup_code(session: Session, request_id: str) -> None:
    send_signup_code(session, _open_request(session, request_id))


def verify_signup(
    session: Session, *, request_id: str, code: str, ip: str | None
) -> tuple[User, Tenant]:
    """The code right -> the Fleek organisation, then the firm and its owner. One transaction: if anything
    after the Fleek call fails, nothing is kept here and the (idempotent) call can simply be repeated."""
    req = _open_request(session, request_id)
    if not auth_throttle.hit(session, "signup_verify", request_id, limit=12, window_seconds=1800):
        raise CodeError("Too many wrong codes. Please start again.", status=429)
    wa = auth_codes.check(session, purpose="signup", subject_id=req.id, channel="whatsapp", code=code)
    if wa is None:
        raise CodeError("That code is wrong or has expired. Check it and try again.", status=400)
    if _taken(session, req.mobile):  # someone finished the same mobile meanwhile
        raise CodeError(CLASH, status=409)

    try:
        status, body = fleek_request(
            "POST",
            SELF_SIGNUP_PATH,
            {
                "request_id": req.id,
                "shop_name": req.firm_name,
                "owner_name": req.owner_name,
                "email": req.email or "",
                "mobile": req.mobile,
            },
        )
    except FleekUnavailable:
        raise CodeError(FLEEK_DOWN, status=503) from None
    if status == 409:
        raise CodeError(CLASH, status=409)
    org_id = body.get("org_id")
    if status not in (200, 201) or not org_id:
        raise CodeError(FLEEK_DOWN, status=503)

    now = datetime.now(UTC)
    tenant = firm_provisioning.create_firm(session, legal_name=req.firm_name, fleek_org_id=org_id)
    tenant.signup_source = "self"
    tenant.email = req.email
    tenant.phone = req.mobile
    user = User(
        tenant_id=tenant.id,
        email=req.email,
        password_hash=req.password_hash,
        role=UserRole.owner,
        mobile=req.mobile,
        mobile_verified_at=now,
        terms_accepted_at=req.terms_accepted_at,
    )
    session.add(user)
    auth_codes.consume(session, wa)
    req.status = "done"
    req.tenant_id = tenant.id
    req.fleek_org_id = org_id
    session.flush()
    activity.record(session, tenant_id=tenant.id, user_id=user.id, key="signed_up")
    return user, tenant


# ------------------------------------------------------------------------------------ password reset


def start_reset(session: Session, *, identifier: str, ip: str | None) -> None:
    """Silent on an unknown account: the caller always shows the same answer. The code goes to the WhatsApp
    number on the account; only an older account with no mobile is sent it by email."""
    if not auth_throttle.hit(session, "reset_ip", ip or "unknown", limit=20, window_seconds=3600):
        raise CodeError("Too many attempts. Try again later.", status=429)
    user = find_user(session, identifier)
    if user is None or not user.is_active or user.is_platform_admin:
        return
    by_whatsapp = bool(user.mobile)
    if not by_whatsapp and not user.email:
        return
    try:
        auth_codes.issue(
            session,
            purpose="reset",
            subject_id=user.id,
            channel="whatsapp" if by_whatsapp else "email",
            target=user.mobile if by_whatsapp else user.email,  # type: ignore[arg-type]
            deliver=deliver_whatsapp if by_whatsapp else make_email_deliverer("reset"),
        )
    except CodeError as exc:
        # asked again too soon, hourly cap, or Fleek could not send: all account-specific, so none of them may
        # change what the caller sees (it would reveal that the account exists). The person simply asks again.
        log.warning("reset code not sent for user %s: %s", user.id, exc)


def finish_reset(session: Session, *, identifier: str, code: str, new_password: str, ip: str | None) -> User:
    key = f"{identity_key(identifier)}|{ip}"
    wrong = CodeError("That code is wrong or has expired.", status=400)
    if auth_throttle.blocked(session, "reset_verify", key, limit=10, window_seconds=900):
        raise CodeError("Too many wrong codes. Try again in 15 minutes.", status=429)
    user = find_user(session, identifier)
    if user is None or not user.is_active or user.is_platform_admin:
        auth_throttle.hit(session, "reset_verify", key, limit=10, window_seconds=900)
        raise wrong
    row = None
    for channel in ("email", "whatsapp"):
        row = auth_codes.check(session, purpose="reset", subject_id=user.id, channel=channel, code=code)
        if row is not None:
            break
    if row is None:
        auth_throttle.hit(session, "reset_verify", key, limit=10, window_seconds=900)
        raise wrong
    bad = passwords.problem(new_password, email=user.email, mobile=user.mobile)
    if bad:
        raise CodeError(bad, status=422)
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    user.must_change_password = False
    if row.channel == "email":
        user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    else:  # the code reached that WhatsApp number, so an operator-typed mobile is now proved
        user.mobile_verified_at = user.mobile_verified_at or datetime.now(UTC)
    auth_codes.consume(session, row)
    auth_throttle.clear(session, "login_fail", key)
    session.flush()
    return user

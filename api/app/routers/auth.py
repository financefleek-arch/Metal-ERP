"""Auth routes: sign up (verified), sign in, reset or change a password, read the current user.

Sign-up needs both a mobile and an email code and creates the firm in Fleek first (docs/PLAN-
onboarding-and- account.md). The public doors are behind a CAPTCHA and database-backed limits.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.models import Tenant, User
from app.models._mixins import UserRole
from app.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LicenceOut,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SignupResendRequest,
    SignupStarted,
    SignupStartRequest,
    SignupVerifyRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.services import account, auth_throttle, captcha, entitlements, features, firm_provisioning, passwords
from app.services.auth_codes import CodeError
from app.services.ratelimit import client_key

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOCKOUT_WINDOW = 15 * 60
_NO_SUCH_USER_HASH = hash_password("not-a-real-password")
LOGIN_BAD = "Incorrect email or password"
LOCKED = "Too many sign-in attempts. Try again in 15 minutes, or reset your password."


def _http(exc: CodeError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _token(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=str(user.role),
            token_version=user.token_version,
        )
    )


# ------------------------------------------------------------------------------------------ sign-up


@router.post("/signup/start", response_model=SignupStarted)
def signup_start(body: SignupStartRequest, request: Request, session: SessionDep) -> SignupStarted:
    """Step 1: validate, CAPTCHA, then send a WhatsApp code to the mobile and an email code. Makes no firm."""
    ip = client_key(request)
    if not body.accept_terms:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Please accept the terms to continue")
    captcha.verify(body.captcha_token, ip)
    try:
        started = account.start_signup(
            session,
            firm_name=body.firm_name,
            owner_name=body.owner_name,
            email=body.email,
            mobile=body.mobile,
            password=body.password,
            ip=ip,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except CodeError as exc:
        raise _http(exc) from None
    return SignupStarted(
        request_id=started.request_id,
        mobile_masked=started.mobile_masked,
        email_masked=started.email_masked,
    )


@router.post("/signup/resend", status_code=status.HTTP_204_NO_CONTENT)
def signup_resend(body: SignupResendRequest, session: SessionDep) -> None:
    try:
        account.resend_signup_code(session, body.request_id, body.channel)
    except CodeError as exc:
        raise _http(exc) from None


@router.post("/signup/verify", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup_verify(body: SignupVerifyRequest, request: Request, session: SessionDep) -> TokenResponse:
    """Step 2: both codes right -> the Fleek org (Free plan), then the firm and its owner, then a token."""
    try:
        user, _ = account.verify_signup(
            session,
            request_id=body.request_id,
            whatsapp_code=body.whatsapp_code,
            email_code=body.email_code,
            ip=client_key(request),
        )
    except CodeError as exc:
        raise _http(exc) from None
    return _token(user)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, session: SessionDep) -> TokenResponse:
    """The old one-call registration: no verification and no Fleek organisation. Off in production
    (`REGISTRATION_OPEN` unset); the test suite and a developer laptop use it to make a firm quickly."""
    if not get_settings().registration_open:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    email = body.email.lower().strip()
    if session.scalar(select(User).where(func.lower(User.email) == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    tenant = firm_provisioning.create_firm(session, legal_name=body.firm_name)
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(body.password),
        role=UserRole.owner,
    )
    session.add(user)
    session.flush()
    return _token(user)


# ------------------------------------------------------------------------------------------ sign-in


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, session: SessionDep) -> TokenResponse:
    email = body.email.lower().strip()
    ip = client_key(request)
    pair = f"{email}|{ip}"
    # Three limits, so one person cannot lock the owner out by guessing from afar (the pair), yet a guesser
    # cannot try without end from one address (the ip) or hammer one account from many (the email).
    if (
        auth_throttle.blocked(session, "login_fail", pair, limit=5, window_seconds=LOCKOUT_WINDOW)
        or auth_throttle.blocked(session, "login_email", email, limit=25, window_seconds=LOCKOUT_WINDOW)
        or auth_throttle.blocked(session, "login_ip", ip, limit=40, window_seconds=LOCKOUT_WINDOW)
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED)

    # The schema allows the same email in two tenants; sign-up and the operator paths enforce global
    # uniqueness, but stay defensive so a stray duplicate cannot turn login into a 500.
    user = session.scalars(
        select(User).where(func.lower(User.email) == email).order_by(User.created_at)
    ).first()
    # an unknown email costs the same time as a wrong password, so the two cannot be told apart by timing
    hashed = user.password_hash if user is not None else _NO_SUCH_USER_HASH
    if not verify_password(body.password, hashed) or user is None:
        for kind, key in (("login_fail", pair), ("login_email", email), ("login_ip", ip)):
            auth_throttle.record(session, kind, key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_BAD)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    auth_throttle.clear(session, "login_fail", pair)
    return _token(user)


# --------------------------------------------------------------------------------- password: forgot


@router.post("/password/forgot", status_code=status.HTTP_204_NO_CONTENT)
def password_forgot(body: ForgotPasswordRequest, request: Request, session: SessionDep) -> None:
    """Send a reset code. The answer is the same whether or not the email has an account."""
    ip = client_key(request)
    captcha.verify(body.captcha_token, ip)
    try:
        account.start_reset(session, email=body.email, channel=body.channel, ip=ip)
    except CodeError as exc:  # only the per-address limit reaches here; account-specific refusals stay silent
        raise _http(exc) from None


@router.post("/password/reset", response_model=TokenResponse)
def password_reset(body: ResetPasswordRequest, request: Request, session: SessionDep) -> TokenResponse:
    try:
        user = account.finish_reset(
            session,
            email=body.email,
            code=body.code,
            new_password=body.new_password,
            ip=client_key(request),
        )
    except CodeError as exc:
        raise _http(exc) from None
    return _token(user)


# ----------------------------------------------------------------------- password: change, sign out


@router.post("/change-password", response_model=TokenResponse)
def change_password(body: ChangePasswordRequest, user: CurrentUser, session: SessionDep) -> TokenResponse:
    """The signed-in person chooses a new password. Every other session ends; this one gets a fresh token."""
    key = f"user:{user.id}"
    if auth_throttle.blocked(session, "pw_change", key, limit=5, window_seconds=LOCKOUT_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again later.")
    if not verify_password(body.current_password, user.password_hash):
        auth_throttle.hit(session, "pw_change", key, limit=5, window_seconds=LOCKOUT_WINDOW)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is not right")
    bad = passwords.problem(body.new_password, email=user.email)
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, bad)
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a password you have not used just now"
        )
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    user.must_change_password = False
    session.flush()
    return _token(user)


@router.post("/sign-out-others", response_model=TokenResponse)
def sign_out_others(user: CurrentUser, session: SessionDep) -> TokenResponse:
    """End every other session of this user (a lost phone). This device stays signed in."""
    user.token_version += 1
    session.flush()
    return _token(user)


# ------------------------------------------------------------------------------------------ me


def _licence_out(session: SessionDep, tenant: Tenant | None) -> LicenceOut | None:
    """The banner data. Never lets a licence problem break sign-in or the bootstrap call."""
    if tenant is None:
        return None
    try:
        lic = entitlements.current(session, tenant)
    except Exception:  # noqa: BLE001
        return None
    if lic is None:
        return None
    return LicenceOut(
        status=lic.status,
        plan_label=lic.plan_label,
        trial_days_left=lic.trial_days_left,
        stale=lic.stale,
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser, session: SessionDep) -> UserOut:
    tenant = session.get(Tenant, user.tenant_id)
    try:
        licence = entitlements.current(session, tenant) if tenant else None
        snap = features.snapshot(session, tenant, licence) if tenant else {}
        free = tenant is not None and features.is_free(session, tenant, licence)
    except Exception:  # noqa: BLE001  a licence problem must not break the bootstrap call
        snap, free = {}, False
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        is_platform_admin=user.is_platform_admin,
        ext_inward_import=bool(tenant and tenant.ext_inward_import),
        ext_supplier_catalog=bool(tenant and tenant.ext_supplier_catalog),
        licence=_licence_out(session, tenant),
        firm_name=tenant.legal_name if tenant else None,
        mobile_masked=account.mask_mobile(user.mobile) if user.mobile else None,
        mobile_verified=user.mobile_verified_at is not None,
        email_verified=user.email_verified_at is not None,
        must_change_password=user.must_change_password,
        plan_tier="free" if free else "full",
        features=snap,
    )

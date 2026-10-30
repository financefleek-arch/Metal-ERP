"""Auth routes: sign up (verified), sign in, reset or change a password, read the current user.

Sign-up needs one WhatsApp code to the mobile (the sign-in; the email is optional) and creates the firm
in Fleek first (docs/PLAN-onboarding-and-account.md). The public doors are behind a CAPTCHA and
database-backed limits.
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
    SetPasswordRequest,
    SignupResendRequest,
    SignupStarted,
    SignupStartRequest,
    SignupVerifyRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.services import (
    account,
    activity,
    auth_throttle,
    captcha,
    entitlements,
    features,
    firm_provisioning,
    passwords,
)
from app.services.auth_codes import CodeError
from app.services.ratelimit import client_key

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOCKOUT_WINDOW = 15 * 60
_NO_SUCH_USER_HASH = hash_password("not-a-real-password")
LOGIN_BAD = "Incorrect mobile number or password"
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


@router.get("/config")
def public_config() -> dict[str, str | None]:
    """What the sign-in page needs before sign-in: the public CAPTCHA widget key (None = no widget)."""
    return {"captcha_site_key": captcha.site_key()}


# ------------------------------------------------------------------------------------------ sign-up


@router.post("/signup/start", response_model=SignupStarted)
def signup_start(body: SignupStartRequest, request: Request, session: SessionDep) -> SignupStarted:
    """Step 1: validate, CAPTCHA, then send a WhatsApp code to the mobile. Makes no firm."""
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
            ad_click=(body.ad_click_kind, body.ad_click_id) if body.ad_click_kind and body.ad_click_id else None,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except CodeError as exc:
        raise _http(exc) from None
    return SignupStarted(
        request_id=started.request_id,
        mobile_masked=started.mobile_masked,
    )


@router.post("/signup/resend", status_code=status.HTTP_204_NO_CONTENT)
def signup_resend(body: SignupResendRequest, session: SessionDep) -> None:
    try:
        account.resend_signup_code(session, body.request_id)
    except CodeError as exc:
        raise _http(exc) from None


@router.post("/signup/callback", status_code=status.HTTP_204_NO_CONTENT)
def signup_callback(body: SignupResendRequest, request: Request, session: SessionDep) -> None:
    """"Ask us to call me", from a code that did not arrive. No phone or SMS here: it lands in Fleek's Follow-ups."""
    if not auth_throttle.hit(session, "signup_callback", client_key(request), limit=5, window_seconds=3600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try again later.")
    try:
        account.request_callback(session, body.request_id)
    except CodeError as exc:
        raise _http(exc) from None


@router.post("/signup/verify", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup_verify(body: SignupVerifyRequest, request: Request, session: SessionDep) -> TokenResponse:
    """Step 2: the code right -> the Fleek org (Free plan), then the firm and its owner, then a token."""
    try:
        user, _ = account.verify_signup(
            session,
            request_id=body.request_id,
            code=body.code,
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
    who = account.identity_key(body.identifier)
    ip = client_key(request)
    pair = f"{who}|{ip}"
    # Three limits, so one person cannot lock the owner out by guessing from afar (the pair), yet a guesser
    # cannot try without end from one address (the ip) or hammer one account from many (the email).
    if (
        auth_throttle.blocked(session, "login_fail", pair, limit=5, window_seconds=LOCKOUT_WINDOW)
        or auth_throttle.blocked(session, "login_email", who, limit=25, window_seconds=LOCKOUT_WINDOW)
        or auth_throttle.blocked(session, "login_ip", ip, limit=40, window_seconds=LOCKOUT_WINDOW)
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED)

    user = account.find_user(session, body.identifier)
    # an unknown account costs the same time as a wrong password, so the two cannot be told apart by timing
    hashed = user.password_hash if user is not None else _NO_SUCH_USER_HASH
    if not verify_password(body.password, hashed) or user is None:
        for kind, key in (("login_fail", pair), ("login_email", who), ("login_ip", ip)):
            auth_throttle.record(session, kind, key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_BAD)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    auth_throttle.clear(session, "login_fail", pair)
    activity.record(session, tenant_id=user.tenant_id, user_id=user.id, key="signed_in")
    activity.touch_seen(user)
    return _token(user)


# --------------------------------------------------------------------------------- password: forgot


@router.post("/password/forgot", status_code=status.HTTP_204_NO_CONTENT)
def password_forgot(body: ForgotPasswordRequest, request: Request, session: SessionDep) -> None:
    """Send a reset code. The answer is the same whether or not the mobile has an account."""
    ip = client_key(request)
    captcha.verify(body.captcha_token, ip)
    try:
        account.start_reset(session, identifier=body.identifier, ip=ip)
    except CodeError as exc:  # only the per-address limit reaches here; account-specific refusals stay silent
        raise _http(exc) from None


@router.post("/password/reset", response_model=TokenResponse)
def password_reset(body: ResetPasswordRequest, request: Request, session: SessionDep) -> TokenResponse:
    try:
        user = account.finish_reset(
            session,
            identifier=body.identifier,
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
    bad = passwords.problem(body.new_password, email=user.email, mobile=user.mobile)
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


@router.post("/set-password", response_model=TokenResponse)
def set_password(body: SetPasswordRequest, user: CurrentUser, session: SessionDep) -> TokenResponse:
    """The first password of a short sign-up, chosen once the owner is in. No current password to give; a second call is refused."""
    if user.password_set:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a password. Use Change password instead.")
    email = (str(body.email).lower().strip() if body.email else None) or user.email
    bad = passwords.problem(body.new_password, email=email, mobile=user.mobile)
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, bad)
    user.password_hash = hash_password(body.new_password)
    user.password_set = True
    if body.email and not user.email:
        user.email = email
    user.token_version += 1
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
        plan_key=lic.plan_key,
        plan_label=lic.plan_label,
        trial_days_left=lic.trial_days_left,
        stale=lic.stale,
    )


def _show_welcome(session: SessionDep, user: User, tenant: Tenant | None) -> bool:
    """The "Your account is ready" page is shown once, to the owner of a firm that signed itself up, until it
    has been seen (or the firm already has a finalised invoice, e.g. an account made before this existed)."""
    if tenant is None or tenant.signup_source != "self" or user.role != UserRole.owner:
        return False
    if user.welcome_seen_at is not None:
        return False
    return not activity.has(session, tenant.id, "invoice_finalised")


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
        label=user.label,
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
        password_set=user.password_set,
        show_welcome=_show_welcome(session, user, tenant),
        pdf_days_left=features.pdf_days_left(tenant) if free and tenant is not None else None,
        plan_tier="free" if free else "full",
        features=snap,
    )

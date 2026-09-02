"""Create (or update) a platform-admin login for the Metal ERP operator.

Platform admins are the only principals allowed to act outside their own
tenant, on `/api/admin/*` only (client-firm + user provisioning). They
live in one dedicated "Fleek Operations" tenant with no real firm data.

Run once per environment:

    python -m tools.make_platform_admin --email you@fleek.com --password 's3cret!!'

Re-running for the same email is safe:
  * promotes an existing user to platform admin (and, with --password, resets it)
  * never creates a second Operations tenant

The account is NOT reachable through the app's /register or /login-created
flows — it is deliberately CLI-only.
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Tenant, User
from app.models._mixins import UserRole
from app.security import hash_password

OPS_TENANT_NAME = "Fleek Operations"


def _ops_tenant(session: Session) -> Tenant:
    t = session.scalars(
        select(Tenant).where(Tenant.legal_name == OPS_TENANT_NAME)
    ).first()
    if t is None:
        t = Tenant(legal_name=OPS_TENANT_NAME, document_label="Invoice")
        session.add(t)
        session.flush()
    return t


def run(session: Session, *, email: str, password: str | None) -> tuple[str, bool]:
    """Returns (user_id, created)."""
    email = email.lower().strip()
    tenant = _ops_tenant(session)

    user = session.scalars(
        select(User).where(func.lower(User.email) == email)
    ).first()

    if user is None:
        if not password:
            raise SystemExit("A new admin needs --password")
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.owner,
            is_platform_admin=True,
            is_active=True,
        )
        session.add(user)
        session.flush()
        return user.id, True

    # Existing user — promote in place.
    user.is_platform_admin = True
    user.is_active = True
    if password:
        user.password_hash = hash_password(password)
    session.flush()
    return user.id, False


def main() -> None:
    ap = argparse.ArgumentParser(description="Create/promote a Metal ERP platform admin")
    ap.add_argument("--email", required=True)
    ap.add_argument(
        "--password",
        help="required for a new account; with an existing one, resets the password",
    )
    args = ap.parse_args()

    with SessionLocal() as session:
        user_id, created = run(session, email=args.email, password=args.password)
        session.commit()

    verb = "created" if created else "updated"
    print(f"platform admin {verb}: {args.email}  (user {user_id}, tenant '{OPS_TENANT_NAME}')")


if __name__ == "__main__":
    main()

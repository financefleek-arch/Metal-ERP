"""Password hashing and JWT issue/verify.

Argon2 for password hashes (via passlib), HS256 JWTs signed with
`settings.jwt_secret`. The token carries the user id (`sub`), tenant id
(`tid`), and role so route dependencies don't need a DB round-trip just
to authorise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

_settings = get_settings()
_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_access_token(*, user_id: str, tenant_id: str, role: str) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "tid": tenant_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_settings.jwt_expiry_minutes)).timestamp()),
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises JWTError on any problem (bad signature, expired, malformed)."""
    return jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm])


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "JWTError",
]

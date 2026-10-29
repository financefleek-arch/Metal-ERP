"""Password rules, one place: the same as Fleek's (8+ characters with a digit or special character)
plus a short list of passwords everyone tries first.
"""

from __future__ import annotations

import re

_HAS_DIGIT_OR_SPECIAL = re.compile(r"[\d\W_]")
_COMMON = {
    "password",
    "password1",
    "password123",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "qwertyuiop",
    "iloveyou1",
    "admin123",
    "welcome1",
    "welcome123",
    "abcd1234",
    "abc12345",
    "11111111",
    "00000000",
    "metalerp",
    "metalerp1",
    "fleek123",
    "india123",
    "india@123",
    "pass@123",
    "password@123",
    "test1234",
}


def problem(
    password: str, *, email: str | None = None, firm: str | None = None, mobile: str | None = None
) -> str | None:
    """A sentence saying what is wrong with the password, or None when it is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if len(password) > 200:
        return "Password is too long"
    if not _HAS_DIGIT_OR_SPECIAL.search(password):
        return "Password must contain at least one digit or special character"
    low = password.lower()
    if low in _COMMON:
        return "That password is too common. Choose another."
    if email and low == email.split("@")[0].lower():
        return "Password must not be your email name"
    if mobile and re.sub(r"\D", "", low) == mobile.lstrip("+")[-10:]:
        return "Password must not be your mobile number"
    if firm and low == firm.strip().lower():
        return "Password must not be your firm name"
    return None

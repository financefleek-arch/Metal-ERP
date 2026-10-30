"""Look an IFSC up so a shop never types its bank or branch.

The open Razorpay IFSC directory is free and keyless. Answers are kept for the life of the process (a branch's
details barely change); a failed call is never kept, so a blip does not stick.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_URL = "https://ifsc.razorpay.com/{code}"
_TIMEOUT = 3.0

# When the directory cannot be reached, the first four letters still name the bank.
BANK_OF_PREFIX = {
    "SBIN": "State Bank of India", "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "UTIB": "Axis Bank",
    "PUNB": "Punjab National Bank", "BARB": "Bank of Baroda", "KKBK": "Kotak Mahindra Bank",
    "CNRB": "Canara Bank", "UBIN": "Union Bank of India", "IDIB": "Indian Bank",
    "BKID": "Bank of India", "YESB": "Yes Bank", "INDB": "IndusInd Bank", "IDFB": "IDFC First Bank",
    "FDRL": "Federal Bank", "MAHB": "Bank of Maharashtra", "CBIN": "Central Bank of India",
}  # fmt: skip


@dataclass(frozen=True)
class Ifsc:
    ifsc: str
    found: bool  # False = the directory has no such code
    checked: bool  # False = the directory could not be reached, so `found` says nothing
    bank: str | None = None
    branch: str | None = None
    city: str | None = None
    upi: bool | None = None
    imps: bool | None = None


@lru_cache(maxsize=4096)
def _lookup_found(code: str) -> Ifsc:
    """A hit or a definite miss is cached; a failed call raises, and an exception is never cached."""
    r = httpx.get(_URL.format(code=code), timeout=_TIMEOUT)
    if r.status_code == 404:
        return Ifsc(code, found=False, checked=True)
    r.raise_for_status()
    d = r.json()
    return Ifsc(
        code,
        found=True,
        checked=True,
        bank=d.get("BANK"),
        branch=(d.get("BRANCH") or "").title() or None,
        city=(d.get("CITY") or "").title() or None,
        upi=d.get("UPI"),
        imps=d.get("IMPS"),
    )


def lookup(code: str) -> Ifsc | None:
    """None when the code is not even shaped like an IFSC."""
    code = code.strip().upper()
    if not IFSC_RE.match(code):
        return None
    try:
        return _lookup_found(code)
    except Exception:  # noqa: BLE001 - any failure to reach the directory falls back to the bank prefix
        log.warning("ifsc lookup unavailable for %s", code[:4], exc_info=True)
        return Ifsc(code, found=False, checked=False, bank=BANK_OF_PREFIX.get(code[:4]))

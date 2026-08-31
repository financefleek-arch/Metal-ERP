"""Static reference data: Indian GST state codes, PAN/GSTIN patterns.

State codes are the GSTIN prefix (first two digits). Kept as code here
rather than a DB table — the list is fixed by statute and tiny.
"""

from __future__ import annotations

import re

# code -> state / UT name
STATE_CODES: dict[str, str] = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (before division)",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

VALID_STATE_CODES = frozenset(STATE_CODES)

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# 2-digit state + 10-char PAN + entity code + 'Z' (default) + checksum char
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def normalize_pan(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    return v or None


def normalize_gstin(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    return v or None


def validate_pan(value: str | None) -> str | None:
    v = normalize_pan(value)
    if v is None:
        return None
    if not PAN_RE.match(v):
        raise ValueError("PAN must be 10 chars: AAAAA9999A")
    return v


def validate_gstin(value: str | None) -> str | None:
    v = normalize_gstin(value)
    if v is None:
        return None
    if not GSTIN_RE.match(v):
        raise ValueError("GSTIN must be 15 chars: 99AAAAA9999A9Z9")
    if v[:2] not in VALID_STATE_CODES:
        raise ValueError(f"GSTIN state prefix '{v[:2]}' is not a valid state code")
    return v


def validate_state_code(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    if v not in VALID_STATE_CODES:
        raise ValueError(f"'{v}' is not a valid GST state code")
    return v

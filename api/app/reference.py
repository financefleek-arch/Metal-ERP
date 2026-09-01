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

# Reverse lookup for imports (Tally writes LEDSTATENAME as a name). Lowercased,
# punctuation-flattened. A few common Tally spellings are aliased.
_STATE_NAME_TO_CODE: dict[str, str] = {}
for _code, _name in STATE_CODES.items():
    _STATE_NAME_TO_CODE[_name.lower()] = _code
_STATE_NAME_TO_CODE.update(
    {
        "jammu and kashmir": "01",
        "dadra and nagar haveli and daman and diu": "26",
        "dadra & nagar haveli": "26",
        "daman & diu": "26",
        "andaman and nicobar islands": "35",
        "pondicherry": "34",
        "orissa": "21",
        "uttaranchal": "05",
        "chattisgarh": "22",
        "andhra pradesh": "37",
    }
)


def state_code_from_name(name: str | None) -> str | None:
    """Map a state *name* (as Tally exports it) to its 2-digit GST code, or None."""
    if not name:
        return None
    key = re.sub(r"[.\-]", " ", name.strip().lower())
    key = re.sub(r"\s+", " ", key).replace(" & ", " and ")
    return _STATE_NAME_TO_CODE.get(key)

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# 2-digit state + 10-char PAN + entity code + 'Z' (default) + checksum char
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# Indian PIN: 6 digits, first not zero.
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")

# Phone: an optional leading +, then 7-15 digits once separators are stripped.
_PHONE_STRIP_RE = re.compile(r"[\s\-().]+")
_PHONE_SHAPE_RE = re.compile(r"^\+?[0-9]{7,15}$")

# Legal name: at least one letter; letters, digits, spaces and a small set of
# business punctuation. Rupee/ampersand shops, "M/s", "S.K. Traders (P) Ltd".
_LEGAL_NAME_ALLOWED_RE = re.compile(r"^[A-Za-z0-9 &.,\-/()'@]+$")
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RUN_RE = re.compile(r"\s{2,}")

# City / town: letters, spaces, and . - ' only.
_CITY_ALLOWED_RE = re.compile(r"^[A-Za-z .\-']+$")

LEGAL_NAME_MAX = 140
ADDRESS_LINE_MAX = 120
CITY_MAX = 60


def _collapse_ws(value: str) -> str:
    return _WS_RUN_RE.sub(" ", value.strip())


# GSTIN check-digit (last char): base-36 weighted mod-36 over the first 14.
_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = _GSTIN_ALPHABET.index(ch)
        p = v * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _GSTIN_ALPHABET[(36 - total % 36) % 36]


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
    if _gstin_check_char(v[:14]) != v[14]:
        raise ValueError("GSTIN check digit is invalid")
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


def validate_phone(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if raw == "":
        return None
    cleaned = _PHONE_STRIP_RE.sub("", raw)
    if not _PHONE_SHAPE_RE.match(cleaned):
        raise ValueError(
            "Phone must be 7-15 digits (an optional leading + for a country code)"
        )
    digits = cleaned.lstrip("+")
    # Normalise a bare 10-digit Indian mobile to +91 form.
    if len(digits) == 10 and not cleaned.startswith("+"):
        return f"+91{digits}"
    return cleaned


def validate_pincode(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    if not PINCODE_RE.match(v):
        raise ValueError("PIN code must be 6 digits and not start with 0")
    return v


def validate_legal_name(value: str) -> str:
    v = _collapse_ws(value)
    if len(v) < 2:
        raise ValueError("Name must be at least 2 characters")
    if len(v) > LEGAL_NAME_MAX:
        raise ValueError(f"Name must be at most {LEGAL_NAME_MAX} characters")
    if not _HAS_LETTER_RE.search(v):
        raise ValueError("Name must contain at least one letter")
    if not _LEGAL_NAME_ALLOWED_RE.match(v):
        raise ValueError(
            "Name may use letters, digits, spaces and & . , - / ( ) ' @ only"
        )
    return v


def validate_address_line(value: str | None) -> str | None:
    if value is None:
        return None
    v = _collapse_ws(value)
    if v == "":
        return None
    if len(v) > ADDRESS_LINE_MAX:
        raise ValueError(f"Address line must be at most {ADDRESS_LINE_MAX} characters")
    if _CONTROL_CHARS_RE.search(v):
        raise ValueError("Address line contains an invalid control character")
    return v


def validate_city(value: str | None) -> str | None:
    if value is None:
        return None
    v = _collapse_ws(value)
    if v == "":
        return None
    if len(v) > CITY_MAX:
        raise ValueError(f"City must be at most {CITY_MAX} characters")
    if not _CITY_ALLOWED_RE.match(v):
        raise ValueError("City may use letters, spaces and . - ' only")
    return v

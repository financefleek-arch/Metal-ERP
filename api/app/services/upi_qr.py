"""The payment QR printed on a bill.

A shop either uploads its own payment QR (we keep the `upi://pay?...` text it encodes) or we build one from its
UPI ID. Either way the bill prints a QR that opens a pay screen with the bill's amount already filled in.
"""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import parse_qsl, quote, urlsplit

import segno

UPI_PREFIX = "upi://pay"


def valid_payload(text: str) -> bool:
    """A UPI pay link that names who to pay (`pa`)."""
    if not text.startswith(UPI_PREFIX):
        return False
    return any(k == "pa" and v for k, v in parse_qsl(urlsplit(text).query, keep_blank_values=True))


def payee_of(text: str) -> str | None:
    for k, v in parse_qsl(urlsplit(text).query):
        if k == "pa" and v:
            return v
    return None


def _link(params: list[tuple[str, str]]) -> str:
    return UPI_PREFIX + "?" + "&".join(f"{k}={quote(v, safe='@.-_')}" for k, v in params)


def payload_for(
    *, upi_id: str | None, payload: str | None, shop_name: str, amount: Decimal | None, bill_no: str | None
) -> str | None:
    """The text to encode, or None when the shop has given nothing to pay to."""
    note = f"Bill {bill_no}" if bill_no else None
    if payload and valid_payload(payload):
        params = parse_qsl(urlsplit(payload).query, keep_blank_values=True)
        if any(k == "sign" for k, _ in params):
            return payload  # a signed link breaks if changed: print it exactly as the shop's app made it
        keep = [(k, v) for k, v in params if k not in {"am", "tn", "cu"}]
        if amount:
            keep.append(("am", f"{amount:.2f}"))
        keep.append(("cu", "INR"))
        if note:
            keep.append(("tn", note))
        return _link(keep)
    if upi_id:
        params = [("pa", upi_id.strip()), ("pn", shop_name)]
        if amount:
            params.append(("am", f"{amount:.2f}"))
        params.append(("cu", "INR"))
        if note:
            params.append(("tn", note))
        return _link(params)
    return None


def svg_data_uri(text: str) -> str:
    return segno.make(text, error="m").svg_data_uri(scale=3, border=1)

"""GST e-invoice signed-QR (IRP JWT) decode.

If the PDF embeds the signed QR from the Invoice Registration Portal, its
payload is a JWT whose body carries supplier/buyer GSTIN, doc no/date,
grand total, HSN summary and the IRN — the highest-confidence source for the
money fields. The line-level text parse still runs for descriptions/qty.

Detection is conservative: we only treat a QR as an e-invoice QR when it
decodes to a JWT (`xxx.yyy.zzz`, base64url) with the IRP claim shape. A plain
UPI "scan to pay" QR (like the Sugal Foods sample's page 2) is ignored and
`decode()` returns None — the caller falls through to the table path.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

import pdfplumber

try:  # pragma: no cover - optional, only needed if a real QR is present
    from pyzbar.pyzbar import decode as _zbar_decode
except Exception:  # pyzbar/zbar not installed — QR path simply stays unavailable
    _zbar_decode = None


@dataclass
class QrExtraction:
    supplier_gstin: str | None
    buyer_gstin: str | None
    bill_no: str | None
    bill_date: str | None
    grand_total: str | None
    irn: str | None


def _b64url_json(segment: str) -> dict | None:
    pad = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + pad)
        return json.loads(raw)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None


def _parse_irp_jwt(text: str) -> QrExtraction | None:
    parts = text.split(".")
    if len(parts) != 3:
        return None
    body = _b64url_json(parts[1])
    if not body:
        return None
    # IRP payload nests the invoice under "data" as a JSON string, or inline.
    data = body.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        data = body
    seller = data.get("SellerGstin") or data.get("sellerGstin")
    if not seller:
        return None
    return QrExtraction(
        supplier_gstin=seller,
        buyer_gstin=data.get("BuyerGstin") or data.get("buyerGstin"),
        bill_no=data.get("DocNo") or data.get("docNo"),
        bill_date=data.get("DocDt") or data.get("docDt"),
        grand_total=str(data.get("TotInvVal") or data.get("totInvVal") or "") or None,
        irn=data.get("Irn") or data.get("irn") or body.get("iss"),
    )


def decode(pdf_path: str) -> QrExtraction | None:
    """Return QR-sourced fields, or None if no IRP signed-QR is present."""
    if _zbar_decode is None:
        return None
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                im = page.to_image(resolution=200).original
                for sym in _zbar_decode(im):
                    payload = sym.data.decode("utf-8", "ignore")
                    parsed = _parse_irp_jwt(payload)
                    if parsed is not None:
                        return parsed
    except Exception:
        return None
    return None

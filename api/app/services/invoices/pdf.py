"""Render a finalized invoice to an A4 PDF (WeasyPrint + Jinja2).

`render_invoice_pdf` is pure-ish: it reads the invoice, renders bytes,
writes them under `settings.pdf_dir`, and sets `invoice.pdf_path` +
`pdf_status`. Called from finalize (best-effort) and `POST /rerender`.

The template mirrors the sample layout in
`docs/visual-plan/InvoicePrint.dc.html`, minus every GST element (M1 is
`template_version = "v1-nongst"`). The React live preview in the editor
reuses the same HTML structure + CSS so the screen and the print agree.

WeasyPrint needs Pango/GObject native libs (present in the prod image, not
on a bare Windows dev box). The import is local so a dev machine without
them still boots the API; finalize catches the ImportError and flags the
invoice `pdf_status = failed`.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Invoice, Party, PartyAddress, Tenant
from app.models._mixins import InvoiceStatus, PdfStatus
from app.services import upi_qr
from app.services.invoices.common import measure_for
from app.services.payments import (
    balance_due_for_invoice,
    paid_amount_for_invoice,
    previous_outstanding_for_party,
)

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt_money(value: object) -> str:
    if value is None:
        return ""
    from decimal import Decimal

    d = Decimal(str(value))
    neg = d < 0
    d = abs(d)
    whole, frac = divmod(int(d * 100), 100)
    out = f"{_indian_group(whole)}.{frac:02d}"
    return f"-{out}" if neg else out


def _indian_group(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def _fmt_kg(value: object) -> str:
    if value is None:
        return "0.000"
    from decimal import Decimal

    d = Decimal(str(value))
    whole, frac = divmod(int((abs(d) * 1000).to_integral_value()), 1000)
    return f"{_indian_group(whole)}.{frac:03d}"


def _fmt_uom(value: object) -> str:
    """Unit as a person expects to read it on a printed bill. Storage stays
    canonical ("nos"); shops and their customers say "pcs"."""
    from app.domain.units import normalize_uom

    v = normalize_uom(str(value) if value is not None else "")
    return "pcs" if v == "nos" else v


_env.filters["money"] = _fmt_money
_env.filters["kg"] = _fmt_kg
_env.filters["uom"] = _fmt_uom


def _addr_lines(addr: PartyAddress | None) -> list[str]:
    if addr is None:
        return []
    bits = [addr.line1, addr.line2, addr.line3]
    tail = ", ".join(b for b in (addr.city, addr.state_code) if b)
    if addr.pincode:
        tail = f"{tail} - {addr.pincode}" if tail else addr.pincode
    return [b for b in [*bits, tail] if b]


def render_invoice_pdf(session: Session, invoice: Invoice) -> Path:
    from weasyprint import HTML  # local: keep API bootable without native libs

    settings = get_settings()
    tenant = session.get(Tenant, invoice.tenant_id)
    party = session.get(Party, invoice.party_id)

    bill_to = (
        session.get(PartyAddress, invoice.bill_to_addr_id)
        if invoice.bill_to_addr_id
        else _default_address(party)
    )
    ship_to = (
        session.get(PartyAddress, invoice.ship_to_addr_id)
        if invoice.ship_to_addr_id
        else bill_to
    )

    measure = measure_for(invoice)

    # Payment/balance — only meaningful once finalized (a draft has no frozen
    # grand_total to bill against); a fully-paid bill omits the line entirely
    # rather than printing a redundant "Balance due: 0.00".
    paid_amount = None
    balance_due = None
    # Previous outstanding = everything the party owed us NOT counting this
    # bill (opening balance + other finalized invoices' balance_due − credit).
    # Printed as: "Previous outstanding" + "Grand Total (this invoice)" =
    # "Total amount due". Shown only when it's non-zero.
    previous_outstanding = None
    total_amount_due = None
    if invoice.status == InvoiceStatus.final:
        paid_amount = paid_amount_for_invoice(session, invoice.id)
        balance_due = balance_due_for_invoice(session, invoice)
        if invoice.party_id:
            prev = previous_outstanding_for_party(
                session, invoice.party_id, exclude_invoice_id=invoice.id
            )
            if prev != 0:
                previous_outstanding = prev
                total_amount_due = prev + (invoice.grand_total or 0)

    html = _env.get_template("invoice_v1_nongst.html").render(
        doc_label=(tenant.document_label if tenant else "Invoice"),
        tenant=tenant,
        invoice=invoice,
        party=party,
        bill_to_lines=_addr_lines(bill_to),
        ship_to_lines=_addr_lines(ship_to),
        lines=sorted(invoice.lines, key=lambda x: x.sl_no),
        measure=measure,
        # segment_no of the last line in each segment -> the slip to print after it
        seg_break_after={s.line_to: s for s in measure.segments},
        paid_amount=paid_amount,
        balance_due=balance_due,
        previous_outstanding=previous_outstanding,
        total_amount_due=total_amount_due,
        # a bill with a balance already paid asks for what is still due, not the grand total
        upi_qr_src=_upi_qr_src(tenant, balance_due if balance_due is not None else invoice.grand_total, invoice.number),
    )

    out_dir = Path(settings.pdf_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"invoice-{invoice.id}.pdf"
    HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf(str(out_path))

    invoice.pdf_path = str(out_path)
    invoice.pdf_status = PdfStatus.rendered
    session.flush()
    return out_path


# What the template reads from `tenant`; the preview copies these from the saved firm and lays the
# unsaved edits over them.
PREVIEW_TENANT_FIELDS = (
    "legal_name", "trade_name", "pan", "address", "city", "pincode", "phone",
    "gst_enabled", "gstin", "bank_holder", "bank_name", "bank_ac_no", "bank_ifsc", "bank_branch",
    "upi_id", "upi_qr_payload", "declaration_text", "terms_text", "jurisdiction_text", "document_label",
)  # fmt: skip


def _upi_qr_src(tenant: object, amount: object, bill_no: str | None) -> str | None:
    """The bill's payment QR as an inline image, or None when the firm has given no UPI."""
    from decimal import Decimal

    text = upi_qr.payload_for(
        upi_id=getattr(tenant, "upi_id", None),
        payload=getattr(tenant, "upi_qr_payload", None),
        shop_name=getattr(tenant, "trade_name", None) or getattr(tenant, "legal_name", None) or "",
        amount=Decimal(str(amount)) if amount else None,
        bill_no=bill_no,
    )
    return upi_qr.svg_data_uri(text) if text else None


def render_bill_preview_html(tenant: Tenant, edits: dict[str, object]) -> str:
    """The real bill template filled with a sample sale and the firm's current (possibly unsaved)
    details. It is the same file the PDF is made from, so the preview cannot drift from the print."""
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from app.domain.tax import amount_in_words

    firm = SimpleNamespace(**{f: getattr(tenant, f, None) for f in PREVIEW_TENANT_FIELDS})
    for key, value in edits.items():
        if key in PREVIEW_TENANT_FIELDS:
            setattr(firm, key, value)
    gstin = (firm.gstin or "").strip().upper()
    if firm.gst_enabled and len(gstin) == 15:
        firm.pan = gstin[2:12]  # the same read the save makes
    if firm.gst_enabled and not gstin:
        firm.gst_enabled = False  # a GST bill with no GSTIN prints nothing extra
    firm.legal_name = firm.legal_name or "Your shop name"

    def line(n: int, name: str, hsn: str, qty: str, rate: str) -> SimpleNamespace:
        q, r = Decimal(qty), Decimal(rate)
        return SimpleNamespace(
            sl_no=n, description=name, hsn_code=hsn, quantity=q, uom="kg", unit_rate=r,
            discount=Decimal("0"), discount_pct=None, line_total=q * r,
        )  # fmt: skip

    lines = [
        line(1, "Brass sheet 2mm", "", "12.400", "520"),
        line(2, "Copper wire 1.5mm", "", "8.150", "800"),
        line(3, "Steel patila 5 kg", "", "6.000", "260"),
    ]
    subtotal = sum((ln.line_total for ln in lines), Decimal("0"))
    previous = Decimal("18450")
    invoice = SimpleNamespace(
        number="0042", date=date.today(), series="INV", fy="2026-27",
        discount_total=Decimal("0"), subtotal=subtotal, taxable_total=subtotal,
        round_off=Decimal("0"), grand_total=subtotal,
        amount_in_words=amount_in_words(subtotal), notes=None,
        declaration_snapshot=firm.declaration_text, terms_snapshot=firm.terms_text,
    )  # fmt: skip
    party = SimpleNamespace(legal_name="Sample Customer", pan=None)
    return _env.get_template("invoice_v1_nongst.html").render(
        preview=True,
        upi_qr_src=_upi_qr_src(firm, subtotal, invoice.number),
        doc_label=firm.document_label or "Invoice",
        tenant=firm,
        invoice=invoice,
        party=party,
        bill_to_lines=["Sample address, Your city"],
        ship_to_lines=["Sample address, Your city"],
        lines=lines,
        measure=None,
        seg_break_after={},
        paid_amount=None,
        balance_due=None,
        previous_outstanding=previous,
        total_amount_due=previous + subtotal,
    )


def _default_address(party: Party | None) -> PartyAddress | None:
    if party is None or not party.addresses:
        return None
    for a in party.addresses:
        if a.is_default:
            return a
    return party.addresses[0]

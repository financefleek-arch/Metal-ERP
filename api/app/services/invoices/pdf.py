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
from app.services.invoices.common import measure_for
from app.services.payments import balance_due_for_invoice, paid_amount_for_invoice

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


_env.filters["money"] = _fmt_money
_env.filters["kg"] = _fmt_kg


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
    if invoice.status == InvoiceStatus.final:
        paid_amount = paid_amount_for_invoice(session, invoice.id)
        balance_due = balance_due_for_invoice(session, invoice)

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
    )

    out_dir = Path(settings.pdf_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"invoice-{invoice.id}.pdf"
    HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf(str(out_path))

    invoice.pdf_path = str(out_path)
    invoice.pdf_status = PdfStatus.rendered
    session.flush()
    return out_path


def _default_address(party: Party | None) -> PartyAddress | None:
    if party is None or not party.addresses:
        return None
    for a in party.addresses:
        if a.is_default:
            return a
    return party.addresses[0]

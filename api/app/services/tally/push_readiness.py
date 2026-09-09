"""F1b-1 / F5d — "can this invoice / inward bill be pushed to Tally right now?"

`push_blockers()` / `purchase_push_blockers()` never raise; an empty list
means pushable. Used both by the finalize/approve-time best-effort enqueue
(which must silently no-op on any blocker — most invoices/bills, most
firms, will have one forever, and that's fine) and by the manual push
button / status endpoint (which surfaces the blocker text so the
operator/firm knows exactly what's missing).

`sales_ledger` is the only `ledger_map` key `push_blockers()` checks —
round-off is folded into the last line's amount by the sales serializer,
never a separate ledger, so `round_off_ledger` is not required (see
`app/services/invoices/tally_xml.py` and the F1b-1 plan's "Live probe
findings"). `purchase_push_blockers()` requires `purchase_ledger` +
`round_off_ledger` instead — the purchase voucher's existing, live-
verified shape (F5d plan, 2026-09-09 live probe) uses a real standalone
Round Off ledger line, unlike sales.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice, InwardBill, Item, Party, TallyCompany
from app.models._mixins import InwardStatus

_MAX_LISTED_ITEMS = 3


@dataclass
class PushBlocker:
    code: str
    message: str


def _real_lines(invoice: Invoice) -> list:
    return [ln for ln in invoice.lines if (ln.description or "").strip()]


def get_tally_company(session: Session, tenant_id: str) -> TallyCompany | None:
    return session.scalar(select(TallyCompany).where(TallyCompany.tenant_id == tenant_id))


def push_blockers(session: Session, invoice: Invoice) -> list[PushBlocker]:
    blockers: list[PushBlocker] = []

    company = get_tally_company(session, invoice.tenant_id)
    if company is None:
        blockers.append(
            PushBlocker(
                "no_tally_company",
                "This firm has no Tally company linked yet.",
            )
        )
        return blockers  # nothing else is checkable without a company

    if not company.shop_id:
        blockers.append(
            PushBlocker(
                "no_tally_company",
                "This firm's Tally company has no companion agent linked yet.",
            )
        )

    if not (company.ledger_map or {}).get("sales_ledger"):
        blockers.append(
            PushBlocker(
                "ledger_map_incomplete",
                "The Sales ledger isn't mapped for this firm's Tally company.",
            )
        )

    lines = _real_lines(invoice)
    if not lines:
        blockers.append(PushBlocker("no_lines", "This invoice has no line items."))

    if invoice.party_id is None or invoice.party is None:
        blockers.append(PushBlocker("party_not_linked", "This invoice has no party."))
    elif not invoice.party.tally_guid:
        blockers.append(
            PushBlocker(
                "party_not_linked",
                f"Party '{invoice.party.legal_name}' isn't linked to Tally yet "
                "— pull masters after adding it in Tally, or add it there by hand.",
            )
        )

    # `InvoiceLine.item` is a `viewonly=True, lazy="joined"` relationship —
    # loaded once when the line was first fetched, so it can go stale
    # within the same session/transaction that just wrote `ln.item_id`
    # (e.g. finalize's item-resolution step, run moments before this check
    # in the same best-effort finalize-time push). `session.get()` always
    # reflects the identity map's current state, so use that instead of
    # trusting `ln.item` directly.
    unlinked_item_names: list[str] = []
    for ln in lines:
        item = session.get(Item, ln.item_id) if ln.item_id else None
        if item is None or not item.tally_guid:
            unlinked_item_names.append(item.name if item else ln.description)
    if unlinked_item_names:
        shown = unlinked_item_names[:_MAX_LISTED_ITEMS]
        extra = len(unlinked_item_names) - len(shown)
        names = ", ".join(shown) + (f" +{extra} more" if extra > 0 else "")
        blockers.append(
            PushBlocker(
                "item_not_linked",
                f"These items aren't linked to Tally yet: {names}.",
            )
        )

    return blockers


def purchase_push_blockers(session: Session, bill: InwardBill) -> list[PushBlocker]:
    """F5d — strict mode, mirrors `push_blockers()`. A staged-new supplier
    or item (created locally at approve time but never pushed as a Tally
    master) counts as unlinked here — same strict-mode rule as sales:
    this slice does not auto-create masters at push time.
    """
    blockers: list[PushBlocker] = []

    if bill.status != InwardStatus.approved:
        blockers.append(PushBlocker("not_approved", "This bill has not been approved yet."))
        return blockers

    company = get_tally_company(session, bill.tenant_id)
    if company is None:
        blockers.append(
            PushBlocker("no_tally_company", "This firm has no Tally company linked yet.")
        )
        return blockers

    if not company.shop_id:
        blockers.append(
            PushBlocker(
                "no_tally_company",
                "This firm's Tally company has no companion agent linked yet.",
            )
        )

    ledger_map = company.ledger_map or {}
    missing_ledgers = [
        label
        for key, label in (("purchase_ledger", "Purchase"), ("round_off_ledger", "Round Off"))
        if not ledger_map.get(key)
    ]
    if missing_ledgers:
        blockers.append(
            PushBlocker(
                "ledger_map_incomplete",
                f"These ledgers aren't mapped for this firm's Tally company: "
                f"{', '.join(missing_ledgers)}.",
            )
        )

    lines = list(bill.lines)
    if not lines:
        blockers.append(PushBlocker("no_lines", "This bill has no line items."))

    if bill.matched_party_id is None:
        blockers.append(
            PushBlocker(
                "party_not_linked",
                "This bill's supplier was staged as new and hasn't been "
                "pushed to Tally as a master yet — pull masters after "
                "adding it in Tally, or add it there by hand.",
            )
        )
    else:
        party = session.get(Party, bill.matched_party_id)
        if party is None or not party.tally_guid:
            name = party.legal_name if party else bill.supplier_name or "the supplier"
            blockers.append(
                PushBlocker(
                    "party_not_linked",
                    f"Supplier '{name}' isn't linked to Tally yet — pull "
                    "masters after adding it in Tally, or add it there by hand.",
                )
            )

    unlinked_item_names: list[str] = []
    for ln in lines:
        if ln.matched_item_id is None:
            unlinked_item_names.append(ln.description)
            continue
        item = session.get(Item, ln.matched_item_id)
        if item is None or not item.tally_guid:
            unlinked_item_names.append(item.name if item else ln.description)
    if unlinked_item_names:
        shown = unlinked_item_names[:_MAX_LISTED_ITEMS]
        extra = len(unlinked_item_names) - len(shown)
        names = ", ".join(shown) + (f" +{extra} more" if extra > 0 else "")
        blockers.append(
            PushBlocker(
                "item_not_linked",
                f"These items aren't linked to Tally yet: {names}.",
            )
        )

    return blockers

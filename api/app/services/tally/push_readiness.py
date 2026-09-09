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

**F5-e (2026-09-09) — purchase-side auto-create, sales-side unchanged.**
`push_blockers()` (sales/invoices) stays strict mode: an unlinked party or
item still hard-blocks, per F1b-1's own decision (most invoices reference
parties/items that already exist from normal shop operation, so this bit
rarely in practice). `purchase_push_blockers()` (inward bills) no longer
hard-blocks on an unlinked-but-*nameable* party/item — a first-time
vendor bill is the *expected common case* for AP capture, confirmed by
the very first real bill pushed through this pipeline (Sugal Foods:
1 staged-new supplier + 12 staged-new items, zero pre-existing in Tally).
Those now surface as informational `PENDING_MASTER_CREATE` `PushBlocker`s
(still returned, so the UI can say "will create X new masters" — see
`pending_master_creates()`) but do NOT prevent `pushable=True`. A bill is
still hard-blocked if a line has literally no item at all (`item_id is
None` *and* no staged name) — genuinely nothing to push.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice, InwardBill, Item, Party, TallyCompany
from app.models._mixins import InwardStatus

_MAX_LISTED_ITEMS = 3


# Codes that are informational, not blocking — a bill/invoice with only
# these in its blocker list is still pushable=True. Currently just F5-e's
# auto-create notice; kept as a set (not a single string) so a future
# informational code can join it without touching every call site.
_INFO_ONLY_CODES = frozenset({"pending_master_create"})


@dataclass
class PushBlocker:
    code: str
    message: str


def is_pushable(blockers: list[PushBlocker]) -> bool:
    """True if none of `blockers` are hard blockers — i.e. every entry (if
    any) is purely informational (F5-e's "will auto-create" notices)."""
    return all(b.code in _INFO_ONLY_CODES for b in blockers)


@dataclass
class PendingMasterCreates:
    """What `enqueue_push_purchase` will ask Tally to create alongside the
    voucher, in the same envelope — F5-e auto-create. `supplier_name` is
    set only when the bill's party has no `tally_guid` yet (whether it was
    staged-new by approve or matched to a pre-existing, not-yet-Tally-
    linked Party); `item_names` likewise for lines whose item has no
    `tally_guid`. Both empty means nothing to auto-create — the strict-mode
    "fully pre-linked" case.
    """

    supplier_name: str | None
    item_names: set[str]

    @property
    def has_any(self) -> bool:
        return self.supplier_name is not None or bool(self.item_names)


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

    # A line with genuinely nothing to push — no linked item AND no name to
    # even create one from — is a hard blocker; there's no voucher line to
    # build. This should be unreachable for an *approved* bill (approve_gate
    # requires every line resolved, per-item, one way or the other) but
    # stays defensive since it costs nothing to check.
    nameless_lines = [
        ln.sl_no
        for ln in lines
        if ln.matched_item_id is None and not (ln.new_item_staged_json or {}).get("name")
    ]
    if nameless_lines:
        blockers.append(
            PushBlocker(
                "no_lines",
                f"Line(s) {', '.join(str(n) for n in nameless_lines)} have no item "
                "and no name to create one from.",
            )
        )

    # Supplier: a hard blocker only if there's no name at all to create
    # from (shouldn't happen post-approve — approve_gate requires a
    # resolved supplier either way). An unlinked-but-nameable supplier is
    # informational only (F5-e auto-create), not blocking.
    pending = pending_master_creates(session, bill)
    if bill.matched_party_id is None and pending.supplier_name is None:
        blockers.append(
            PushBlocker(
                "party_not_linked",
                "This bill has no resolved supplier to push.",
            )
        )
    elif pending.supplier_name is not None:
        blockers.append(
            PushBlocker(
                "pending_master_create",
                f"Supplier '{pending.supplier_name}' isn't in Tally yet — "
                "will be created automatically when this pushes.",
            )
        )

    if pending.item_names:
        shown = sorted(pending.item_names)[:_MAX_LISTED_ITEMS]
        extra = len(pending.item_names) - len(shown)
        names = ", ".join(shown) + (f" +{extra} more" if extra > 0 else "")
        blockers.append(
            PushBlocker(
                "pending_master_create",
                f"These items aren't in Tally yet: {names} — will be created "
                "automatically when this pushes.",
            )
        )

    return blockers


def pending_master_creates(session: Session, bill: InwardBill) -> PendingMasterCreates:
    """What `enqueue_push_purchase` needs to ask Tally to create alongside
    the voucher (F5-e). Never raises — a bill with nothing resolvable at
    all just returns an empty result; the caller's blocker check above
    handles that as a hard block separately.
    """
    supplier_name: str | None = None
    if bill.matched_party_id is not None:
        party = session.get(Party, bill.matched_party_id)
        if party is not None and not party.tally_guid:
            supplier_name = party.legal_name
    elif bill.new_supplier_staged_json:
        supplier_name = bill.new_supplier_staged_json.get("legal_name") or bill.supplier_name

    item_names: set[str] = set()
    for ln in bill.lines:
        if ln.matched_item_id is not None:
            item = session.get(Item, ln.matched_item_id)
            if item is not None and not item.tally_guid:
                item_names.add(item.name)
        elif ln.new_item_staged_json and ln.new_item_staged_json.get("name"):
            item_names.add(str(ln.new_item_staged_json["name"]))

    return PendingMasterCreates(supplier_name=supplier_name, item_names=item_names)

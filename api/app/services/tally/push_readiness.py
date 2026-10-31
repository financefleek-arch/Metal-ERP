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

from sqlalchemy.orm import Session

from app.models import Invoice, InwardBill, Item, Party, TallyCompany
from app.models._mixins import InwardStatus
from app.services.tally.company_routing import company_for_document
from app.services.tally.links import is_linked
from app.services.tally.master_check import classify, known_item_names, known_supplier_names
from app.services.tally.units import DIFFERS, UNKNOWN, company_line_unit

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
    # S2.7: names already in the company's Tally under another spelling: (ours, Tally's). Creating them
    # would add a duplicate, so the push is held for the user.
    near_supplier: tuple[str, str] | None = None
    near_items: tuple[tuple[str, str], ...] = ()

    @property
    def has_any(self) -> bool:
        return self.supplier_name is not None or bool(self.item_names)


def _real_lines(invoice: Invoice) -> list:
    return [ln for ln in invoice.lines if (ln.description or "").strip()]


def _company_name_blocker() -> PushBlocker:
    # S0.2: the voucher names its company, so an empty name would send it to whichever is open.
    return PushBlocker(
        "company_name_missing",
        "The Tally company has no name set, so we cannot tell Tally where to post this.",
    )


def _wrong_company_blockers(company) -> list[PushBlocker]:
    """S0.3: the agent last saw a different company (or a renamed one) where ours should be. A push
    would be refused or, worse, go to the wrong books, so it waits until the owner relinks."""
    if company.verify_state in ("different", "renamed") and company.verify_note:
        return [PushBlocker(f"company_{company.verify_state}", company.verify_note)]
    return []


def _unit_blockers(
    session: Session, company, found: list[tuple[Item, str | None]]
) -> list[PushBlocker]:
    """S0.5: a line must go to Tally in the item's own unit in the document's company, or Tally posts
    it with a blank quantity and rate and says nothing. `found` is (linked item, the unit the line is
    in)."""
    unknown: list[str] = []
    differs: list[str] = []
    for item, uom in found:
        unit, problem = company_line_unit(session, company, item, uom)
        if problem == UNKNOWN:
            unknown.append(item.name)
        elif problem == DIFFERS:
            line_in = uom or item.uom or "nos"
            differs.append(f"{item.name} (Tally has it in {unit}, this line is in {line_in})")
    out: list[PushBlocker] = []
    if unknown:
        out.append(
            PushBlocker(
                "unit_unknown",
                f"We haven't seen Tally's unit for: {_listed(unknown)}. Pull masters from Tally "
                "to refresh, so a quantity is not lost on the voucher.",
            )
        )
    if differs:
        out.append(
            PushBlocker(
                "unit_mismatch",
                f"These lines are in a different unit from Tally's: {_listed(differs)}. Tally would "
                "drop the quantity, so change the unit on the line.",
            )
        )
    return out


def _listed(names: list[str]) -> str:
    shown = names[:_MAX_LISTED_ITEMS]
    extra = len(names) - len(shown)
    return ", ".join(shown) + (f" +{extra} more" if extra > 0 else "")


def push_blockers(session: Session, invoice: Invoice) -> list[PushBlocker]:
    blockers: list[PushBlocker] = []

    company = company_for_document(session, invoice)
    if company is None:
        blockers.append(
            PushBlocker(
                "no_tally_company",
                "This firm has no Tally company linked yet.",
            )
        )
        return blockers  # nothing else is checkable without a company

    if company.status != "active":
        blockers.append(
            PushBlocker(
                "company_disabled",
                f"The Tally company {company.company_name} is switched off for this firm.",
            )
        )

    if not company.shop_id:
        blockers.append(
            PushBlocker(
                "no_tally_company",
                "This firm's Tally company has no companion agent linked yet.",
            )
        )

    if not (company.company_name or "").strip():
        blockers.append(_company_name_blocker())
    blockers.extend(_wrong_company_blockers(company))

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
    elif not is_linked(session, company, "party", invoice.party):
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
    linked_lines: list[tuple[Item, str | None]] = []
    for ln in lines:
        item = session.get(Item, ln.item_id) if ln.item_id else None
        if item is None or not is_linked(session, company, "item", item):
            unlinked_item_names.append(item.name if item else ln.description)
        else:
            linked_lines.append((item, ln.uom))
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
    blockers.extend(_unit_blockers(session, company, linked_lines))

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

    company = company_for_document(session, bill)
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

    if not (company.company_name or "").strip():
        blockers.append(_company_name_blocker())
    blockers.extend(_wrong_company_blockers(company))

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
    linked_bill_lines: list[tuple[Item, str | None]] = []
    for ln in lines:
        if ln.matched_item_id is not None:
            item = session.get(Item, ln.matched_item_id)
            if item is not None and is_linked(session, company, "item", item):
                linked_bill_lines.append((item, ln.uom))
    blockers.extend(_unit_blockers(session, company, linked_bill_lines))

    pending = pending_master_creates(session, bill, company)
    staged_name = (bill.new_supplier_staged_json or {}).get("legal_name") or bill.supplier_name
    if bill.matched_party_id is None and not staged_name:
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

    if pending.near_supplier is not None or pending.near_items:
        pairs = ([("Supplier", *pending.near_supplier)] if pending.near_supplier else []) + [
            ("Item", ours, theirs) for ours, theirs in pending.near_items
        ]
        listed = "; ".join(
            f"{kind} '{ours}' looks like '{theirs}'"
            for kind, ours, theirs in pairs[:_MAX_LISTED_ITEMS]
        )
        blockers.append(
            PushBlocker(
                "similar_master_in_tally",
                f"Already in Tally under a similar name: {listed}. Link it to the one in Tally "
                "(or rename it here) so a duplicate is not created.",
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


def pending_master_creates(
    session: Session, bill: InwardBill, company: TallyCompany
) -> PendingMasterCreates:
    """What `enqueue_push_purchase` needs to ask Tally to create alongside
    the voucher (F5-e). Never raises — a bill with nothing resolvable at
    all just returns an empty result; the caller's blocker check above
    handles that as a hard block separately.
    """
    supplier_name: str | None = None
    if bill.matched_party_id is not None:
        party = session.get(Party, bill.matched_party_id)
        if party is not None and not is_linked(session, company, "party", party):
            supplier_name = party.legal_name
    elif bill.new_supplier_staged_json:
        supplier_name = bill.new_supplier_staged_json.get("legal_name") or bill.supplier_name

    item_names: set[str] = set()
    for ln in bill.lines:
        if ln.matched_item_id is not None:
            item = session.get(Item, ln.matched_item_id)
            if item is not None and not is_linked(session, company, "item", item):
                item_names.add(item.name)
        elif ln.new_item_staged_json and ln.new_item_staged_json.get("name"):
            item_names.add(str(ln.new_item_staged_json["name"]))

    # S2.7: leave out what the company's Tally already has (a Create would alter it), and flag a
    # near match instead of creating a duplicate.
    near_supplier: tuple[str, str] | None = None
    if supplier_name is not None:
        kind, theirs = classify(supplier_name, known_supplier_names(company))
        if kind == "exact":
            supplier_name = None
        elif kind == "near" and theirs:
            near_supplier = (supplier_name, theirs)
            supplier_name = None
    known_items = known_item_names(company)
    near_items: list[tuple[str, str]] = []
    for name in sorted(item_names):
        kind, theirs = classify(name, known_items)
        if kind == "exact":
            item_names.discard(name)
        elif kind == "near" and theirs:
            near_items.append((name, theirs))
            item_names.discard(name)

    return PendingMasterCreates(
        supplier_name=supplier_name,
        item_names=item_names,
        near_supplier=near_supplier,
        near_items=tuple(near_items),
    )

"""Refill parties and items from what Tally already knows (plan 2026-10-31, docs/PLAN-tally-voucher-import-and-party-prices.md).

Masters tell us WHO and WHAT exist. This module adds what Tally knows about them, without importing a
single past invoice:

  - stock on hand per item, from the stock-item query              -> `item_tally_state.stock_*`
  - what each party owes, from the ledger query                    -> `party_tally_snapshot` and, where a customer
                                                                      has no ledger yet, `party.opening_balance`
  - when each party last bought, and at what rate for each item    -> `party_tally_snapshot`, `party_item_price`

Each function takes already-parsed rows (`light_masters.py`, `voucher_xml.py`) and one company, matches
them to our records, writes, and returns a result the screen can show. Nothing is ever created here.

Matching is by Tally's own id first: the masters pull stored it on `tally_link`, and the light queries
return it, so a row finds its record whatever either side calls it. Only a row with no id (a sales voucher
names its party and item, nothing more; a UI export has no ids) falls back to the name Tally gave the
record (`tally_link.tally_name`), then our own name, compared case- and spacing-insensitively; an
ambiguous name is never matched. Matched by id with a different name, the link's stored name is brought
up to date, so a rename in Tally corrects itself and nothing asks the user.

What matches nothing is not an error: it is either something that exists only in Metal, or something new in
Tally the masters pull has not brought in yet. It is counted and listed, never guessed at.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Item,
    ItemTallyState,
    Party,
    PartyItemPrice,
    PartyTallySnapshot,
    TallyCompany,
    TallyLink,
)
from app.models._mixins import PartyRole
from app.services.payments import has_ledger_history
from app.services.tally.light_masters import ItemRow, LedgerRow, party_tree
from app.services.tally.stock_summary import StockRow, is_adjustment
from app.services.tally.voucher_xml import OTHER, SALES, ParsedVoucher, PendingBill

_CENT = Decimal("0.01")
_ZERO = Decimal(0)


def _key(name: str | None) -> str:
    return " ".join((name or "").casefold().split())


def _index(session: Session, company: TallyCompany, entity_type: str) -> dict[str, str]:
    """name -> record id for one company. A name that points at two records is left out."""
    model = Party if entity_type == "party" else Item
    name_col = Party.legal_name if entity_type == "party" else Item.name
    seen: dict[str, set[str]] = defaultdict(set)
    linked = session.execute(
        select(TallyLink.tally_name, TallyLink.entity_id).where(
            TallyLink.company_id == company.id,
            TallyLink.entity_type == entity_type,
            TallyLink.tally_name.is_not(None),
        )
    ).all()
    for tally_name, entity_id in linked:
        seen[_key(tally_name)].add(entity_id)
    # our own names fill in only where the company has no name for it, so a renamed record keeps
    # matching the name Tally actually uses
    for entity_id, our_name in session.execute(
        select(model.id, name_col).where(model.tenant_id == company.tenant_id)
    ).all():
        key = _key(our_name)
        if key and key not in seen:
            seen[key].add(entity_id)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def _links(session: Session, company: TallyCompany, entity_type: str) -> dict[str, TallyLink]:
    """Tally id -> this company's link, for one kind of record."""
    return {
        link.tally_guid: link
        for link in session.scalars(
            select(TallyLink).where(
                TallyLink.company_id == company.id,
                TallyLink.entity_type == entity_type,
                TallyLink.tally_guid.is_not(None),
            )
        )
    }


class _Matcher:
    """Finds our record for a row: Tally id first, then the name. Notes renames on the way."""

    def __init__(self, session: Session, company: TallyCompany, entity_type: str) -> None:
        self.by_guid = _links(session, company, entity_type)
        self.by_name = _index(session, company, entity_type)
        self.renamed = 0

    def find(self, guid: str | None, name: str) -> str | None:
        link = self.by_guid.get(guid or "")
        if link is not None:
            if name and link.tally_name != name:
                link.tally_name = name  # Tally is the authority on what it calls its own record
                self.renamed += 1
            return link.entity_id
        return self.by_name.get(_key(name))


def _linked_party_ids(session: Session, company: TallyCompany) -> set[str]:
    return set(
        session.scalars(
            select(TallyLink.entity_id).where(
                TallyLink.company_id == company.id, TallyLink.entity_type == "party"
            )
        )
    )


# ---------------------------------------------------------------------------------------- stock


@dataclass
class StockResult:
    total: int = 0
    matched: int = 0
    renamed: int = 0  # matched by id with a different name in Tally: our stored name was corrected
    adjustment_lines: int = 0
    negative: int = 0  # matched rows whose Tally quantity is below zero
    unmatched: list[str] = field(default_factory=list)  # in Tally, not in Metal: new there, or not pulled yet
    missing: list[str] = field(default_factory=list)  # linked here, gone from Tally (full reads only)
    missing_skipped: bool = False  # most of the list vanished at once: left alone, probably a bad read


def items_from_stock_summary(rows: list[StockRow]) -> list[ItemRow]:
    """A UI Stock Summary export has no ids; its rows go through the same code, matched by name."""
    return [
        ItemRow(
            guid="",
            name=r.name,
            group="",
            unit=r.unit,
            qty=r.qty,
            qty_unit=r.unit,
            rate=r.rate,
            value=r.value,
        )
        for r in rows
        if not r.adjustment
    ]


def _too_many_missing(linked: int, missing: int) -> bool:
    """Half the list gone at once is a failed read (Tally half loaded, the wrong company open), not a clear-out."""
    return linked > 20 and missing * 2 > linked


def _note_missing_items(session, company, rows, existing, result) -> None:  # type: ignore[no-untyped-def]
    seen = {r.guid for r in rows if r.guid}
    if not seen:
        return  # a UI export has no ids: nothing to compare
    links = _links(session, company, "item")
    gone = [link for guid, link in links.items() if guid not in seen]
    if _too_many_missing(len(links), len(gone)):
        result.missing_skipped = True
        return
    for link in gone:
        item = session.get(Item, link.entity_id)
        result.missing.append(item.name if item else link.tally_name or link.tally_guid)
        state = existing.get(link.entity_id)
        if state is not None and state.stock_as_of is not None:
            state.stock_qty = state.stock_unit = state.stock_rate = state.stock_value = None
            state.stock_as_of = None


def apply_stock(
    session: Session,
    company: TallyCompany,
    rows: list[ItemRow],
    now: datetime | None = None,
    *,
    complete: bool = False,
) -> StockResult:
    """`complete` says the rows are every stock item Tally has (not just what changed), so an item we
    link that is not among them is gone from Tally: its stock is cleared and it is listed."""
    now = now or datetime.now(UTC)
    match = _Matcher(session, company, "item")
    existing = {
        s.item_id: s
        for s in session.scalars(
            select(ItemTallyState).where(ItemTallyState.company_id == company.id)
        )
    }
    result = StockResult(total=len(rows))
    for row in rows:
        if is_adjustment(row.name):
            result.adjustment_lines += 1
            continue
        item_id = match.find(row.guid, row.name)
        if item_id is None:
            result.unmatched.append(row.name)
            continue
        state = existing.get(item_id)
        if state is None:
            state = ItemTallyState(tenant_id=company.tenant_id, company_id=company.id, item_id=item_id)
            session.add(state)
            existing[item_id] = state
        state.stock_qty = row.qty
        state.stock_unit = row.qty_unit or row.unit
        state.stock_rate = row.rate.quantize(_CENT) if row.rate is not None else None
        state.stock_value = row.value.quantize(_CENT) if row.value is not None else None
        state.stock_as_of = now
        result.matched += 1
        if row.negative:
            result.negative += 1
    result.renamed = match.renamed
    if complete and rows:
        _note_missing_items(session, company, rows, existing, result)
    session.flush()
    return result


# ------------------------------------------------------------------------------- what parties owe


@dataclass
class PartyBalance:
    """What one party owes the firm, from either source (ledger closing balance, or pending bills)."""

    name: str
    outstanding: Decimal  # positive = the party owes the firm, as `party.opening_balance`
    guid: str = ""
    oldest_day: date | None = None
    open_bills: int | None = None
    candidate: bool = True  # False for a ledger that is plainly not a party (Cash, an expense account)


def balances_from_ledgers(rows: list[LedgerRow], groups=None) -> list[PartyBalance]:  # type: ignore[no-untyped-def]
    """`groups` is the shop's group list (`light_masters.parse_groups`): a ledger is a candidate party when
    its group lineage reaches Sundry Debtors or Creditors, however many custom groups lie between."""
    tree = party_tree(groups)
    return [
        PartyBalance(
            name=r.name,
            guid=r.guid,
            outstanding=r.owed_to_firm,
            candidate=tree.role_for(r.group) is not None,
        )
        for r in rows
    ]


def balances_from_bills(bills: list[PendingBill]) -> list[PartyBalance]:
    """Pending bills summed per party, in Tally's sign (a bill's closing is negative when owed to us)."""
    grouped: dict[str, list[PendingBill]] = defaultdict(list)
    for b in bills:
        if b.party:
            grouped[b.party].append(b)
    return [
        PartyBalance(
            name=name,
            outstanding=-sum((b.closing for b in mine), _ZERO),
            oldest_day=min((b.day for b in mine if b.day), default=None),
            open_bills=len(mine),
        )
        for name, mine in grouped.items()
    ]


@dataclass
class OutstandingResult:
    parties_in_report: int = 0
    matched: int = 0
    renamed: int = 0
    opening_set: int = 0  # customers whose opening balance was filled from Tally
    kept_history: int = 0  # has invoices or payments here already: snapshot only
    suppliers: int = 0  # snapshot only: an opening balance is not set from a supplier's ledger
    differences: list[dict] = field(default_factory=list)  # hand-typed figure differs from Tally's
    unmatched: list[str] = field(default_factory=list)  # a party ledger in Tally that Metal does not hold
    missing: list[str] = field(default_factory=list)  # linked here, gone from Tally (full reads only)
    missing_skipped: bool = False


def _snapshot(session: Session, company: TallyCompany, party_id: str) -> PartyTallySnapshot:
    snap = session.scalar(
        select(PartyTallySnapshot).where(
            PartyTallySnapshot.company_id == company.id, PartyTallySnapshot.party_id == party_id
        )
    )
    if snap is None:
        snap = PartyTallySnapshot(
            tenant_id=company.tenant_id, company_id=company.id, party_id=party_id
        )
        session.add(snap)
    return snap


def _note_missing_parties(session, company, balances, result) -> None:  # type: ignore[no-untyped-def]
    seen = {b.guid for b in balances if b.guid}
    if not seen:
        return
    links = _links(session, company, "party")
    gone = [link for guid, link in links.items() if guid not in seen]
    if _too_many_missing(len(links), len(gone)):
        result.missing_skipped = True
        return
    for link in gone:
        party = session.get(Party, link.entity_id)
        result.missing.append(party.legal_name if party else link.tally_name or link.tally_guid)
        snap = session.scalar(
            select(PartyTallySnapshot).where(
                PartyTallySnapshot.company_id == company.id,
                PartyTallySnapshot.party_id == link.entity_id,
            )
        )
        if snap is not None:
            snap.outstanding = snap.outstanding_as_of = snap.oldest_bill_date = snap.open_bills = None


def apply_outstanding(
    session: Session,
    company: TallyCompany,
    balances: list[PartyBalance],
    as_of: date,
    now: datetime | None = None,
    *,
    absent_is_zero: bool = True,
    complete: bool = False,
) -> OutstandingResult:
    """Write what each party owes. A party linked in this company that the source does not mention owes
    nothing when the source lists only open bills (`absent_is_zero`), and is left alone when it is a full
    ledger list (a missing ledger means the party is gone from Tally, which is not a zero balance)."""
    now = now or datetime.now(UTC)
    match = _Matcher(session, company, "party")
    result = OutstandingResult()
    seen: dict[str, PartyBalance] = {}
    for bal in balances:
        party_id = match.find(bal.guid, bal.name)
        if party_id is None:
            if bal.candidate and bal.name not in result.unmatched:
                result.unmatched.append(bal.name)
            continue
        seen[party_id] = bal
    result.parties_in_report = len(seen) + len(result.unmatched)
    result.renamed = match.renamed
    if complete and balances:
        _note_missing_parties(session, company, balances, result)

    ids = set(seen) | (_linked_party_ids(session, company) if absent_is_zero else set())
    for party_id in sorted(ids):
        party = session.get(Party, party_id)
        if party is None:
            continue
        bal = seen.get(party_id)
        outstanding = (bal.outstanding if bal else _ZERO).quantize(_CENT)
        snap = _snapshot(session, company, party_id)
        snap.outstanding = outstanding
        snap.outstanding_as_of = as_of
        snap.oldest_bill_date = bal.oldest_day if bal else None
        snap.open_bills = bal.open_bills if bal is not None else 0
        snap.refreshed_at = now
        if bal is not None:
            result.matched += 1

        typed = Decimal(party.opening_balance or 0)
        if party.role == PartyRole.supplier:
            result.suppliers += 1
        elif has_ledger_history(session, party_id):
            result.kept_history += 1
        elif typed == _ZERO:
            if outstanding != _ZERO:
                party.opening_balance = outstanding
                party.opening_balance_as_of = as_of
                result.opening_set += 1
        elif typed != outstanding and snap.kept_ours_outstanding != outstanding:
            result.differences.append(
                {
                    "party_id": party_id,
                    "party": party.legal_name,
                    "ours": str(typed),
                    "tally": str(outstanding),
                }
            )
    session.flush()
    return result


# --------------------------------------------------------------------- last purchase and prices


@dataclass
class PriceResult:
    vouchers: int = 0  # sales vouchers read (cancelled, optional and deleted ones are skipped)
    parties: int = 0
    prices: int = 0
    unmatched_parties: list[str] = field(default_factory=list)
    unmatched_items: list[str] = field(default_factory=list)
    # documents with items that are not sales (an Estimate or Sales Order of the shop's own): how many of
    # each type were left out, so the owner can see what a refill did not count and say if it should
    not_counted: dict[str, int] = field(default_factory=dict)


def _line_rate(line) -> Decimal | None:  # type: ignore[no-untyped-def]
    if line.rate is not None and line.rate > 0:
        return line.rate
    if line.qty and line.qty > 0 and line.amount is not None:
        return abs(line.amount) / line.qty  # no rate on the line: what it worked out to
    return None


def apply_sales(
    session: Session,
    company: TallyCompany,
    vouchers: list[ParsedVoucher],
    now: datetime | None = None,
) -> PriceResult:
    """Reduce a window of sales vouchers to: each party's last sale, and the last rate of each item to each
    party. Only the latest of each is kept, so a refresh can be run over any window and in any order."""
    now = now or datetime.now(UTC)
    parties = _index(session, company, "party")
    items = _index(session, company, "item")
    result = PriceResult()

    last_sale: dict[str, tuple[tuple, date, Decimal]] = {}
    count: dict[str, int] = defaultdict(int)
    best: dict[tuple[str, str], tuple[tuple, ParsedVoucher, object]] = {}
    for v in vouchers:
        if v.kind == OTHER and v.items and not (v.cancelled or v.deleted):
            result.not_counted[v.type_name] = result.not_counted.get(v.type_name, 0) + 1
        if v.kind != SALES or v.cancelled or v.optional or v.deleted or v.day is None:
            continue
        result.vouchers += 1
        party_id = parties.get(_key(v.party))
        if party_id is None:
            if v.party and v.party not in result.unmatched_parties:
                result.unmatched_parties.append(v.party)
            continue
        order = (v.day, v.master_id or 0)
        count[party_id] += 1
        if party_id not in last_sale or order > last_sale[party_id][0]:
            last_sale[party_id] = (order, v.day, v.party_amount)
        for line in v.items:
            item_id = items.get(_key(line.item))
            if item_id is None:
                if line.item not in result.unmatched_items:
                    result.unmatched_items.append(line.item)
                continue
            if _line_rate(line) is None:
                continue
            key = (party_id, item_id)
            if key not in best or order > best[key][0]:
                best[key] = (order, v, line)

    for party_id, (_, day, amount) in last_sale.items():
        snap = _snapshot(session, company, party_id)
        if snap.last_sale_date is None or day >= snap.last_sale_date:
            snap.last_sale_date = day
            snap.last_sale_amount = amount.quantize(_CENT)
        snap.sales_in_window = count[party_id]
        snap.refreshed_at = now
    result.parties = len(last_sale)

    existing = {
        (p.party_id, p.item_id): p
        for p in session.scalars(
            select(PartyItemPrice).where(PartyItemPrice.company_id == company.id)
        )
    }
    for (party_id, item_id), (_, v, line) in best.items():
        row = existing.get((party_id, item_id))
        if row is not None and row.sold_on > v.day:
            continue  # what we hold is a later sale than anything in this window
        if row is None:
            row = PartyItemPrice(
                tenant_id=company.tenant_id,
                company_id=company.id,
                party_id=party_id,
                item_id=item_id,
                rate=_ZERO,
                sold_on=v.day,
            )
            session.add(row)
        row.rate = _line_rate(line).quantize(_CENT)
        row.unit = line.rate_unit or line.unit
        row.quantity = line.qty
        row.sold_on = v.day
        row.voucher_no = (v.number or "")[:40] or None
        result.prices += 1
    session.flush()
    return result


# ------------------------------------------------------------------ pending-bill dates (ageing)


@dataclass
class BillDatesResult:
    parties: int = 0
    unmatched: list[str] = field(default_factory=list)
    # a party whose pending bills do not add up to its ledger balance: an on-account receipt, an opening
    # figure typed in Tally. Shown as a count so the shop knows the dates are a guide, not the whole balance.
    off_by_ledger: int = 0


def apply_bill_dates(
    session: Session, company: TallyCompany, bills: list[PendingBill], now: datetime | None = None
) -> BillDatesResult:
    """From Bills Receivable: how many bills each party has open and the oldest one's date. Bills carry the
    party's name only, so they match by the name Tally gave the record. Ageing buckets come from these dates;
    the balance itself stays what the ledger query said."""
    now = now or datetime.now(UTC)
    by_name = _Matcher(session, company, "party").by_name
    grouped: dict[str, list[PendingBill]] = defaultdict(list)
    result = BillDatesResult()
    for bill in bills:
        party_id = by_name.get(_key(bill.party))
        if party_id is None:
            if bill.party and bill.party not in result.unmatched:
                result.unmatched.append(bill.party)
            continue
        grouped[party_id].append(bill)
    for party_id, mine in grouped.items():
        snap = _snapshot(session, company, party_id)
        days = [b.day for b in mine if b.day]
        snap.oldest_bill_date = min(days) if days else None
        snap.open_bills = len(mine)
        snap.refreshed_at = now
        total = (-sum((b.closing for b in mine), _ZERO)).quantize(_CENT)
        if snap.outstanding is not None and abs(total - snap.outstanding) > _CENT:
            result.off_by_ledger += 1
        result.parties += 1
    session.flush()
    return result


# ------------------------------------------------------------ hand-typed vs Tally's balance


def differences(session: Session, company: TallyCompany, limit: int = 200) -> list[dict]:
    """Customers whose typed opening balance differs from Tally's, and that can still take Tally's (no
    ledger yet). One the owner chose to keep is hidden until Tally's figure moves."""
    rows = session.execute(
        select(PartyTallySnapshot, Party)
        .join(Party, Party.id == PartyTallySnapshot.party_id)
        .where(
            PartyTallySnapshot.company_id == company.id,
            PartyTallySnapshot.outstanding.is_not(None),
            Party.role != PartyRole.supplier,
            Party.opening_balance != 0,
            Party.opening_balance != PartyTallySnapshot.outstanding,
        )
        .order_by(Party.legal_name)
    ).all()
    out: list[dict] = []
    for snap, party in rows:
        if snap.kept_ours_outstanding == snap.outstanding or has_ledger_history(session, party.id):
            continue
        out.append(
            {
                "party_id": party.id,
                "party": party.legal_name,
                "ours": str(Decimal(party.opening_balance).quantize(_CENT)),
                "tally": str(snap.outstanding),
                "as_of": snap.outstanding_as_of.isoformat() if snap.outstanding_as_of else None,
            }
        )
        if len(out) >= limit:
            break
    return out


class DifferenceError(ValueError):
    """The choice cannot be made: no such difference, or the party has a ledger here now."""


def settle_difference(session: Session, company: TallyCompany, party_id: str, use: str) -> None:
    snap = session.scalar(
        select(PartyTallySnapshot).where(
            PartyTallySnapshot.company_id == company.id, PartyTallySnapshot.party_id == party_id
        )
    )
    party = session.get(Party, party_id)
    if snap is None or snap.outstanding is None or party is None or party.tenant_id != company.tenant_id:
        raise DifferenceError("No Tally balance on record for this party.")
    if use == "mine":
        snap.kept_ours_outstanding = snap.outstanding
    elif use == "tally":
        if has_ledger_history(session, party_id):
            raise DifferenceError("This party already has invoices or payments here, so its opening balance is locked.")
        party.opening_balance = snap.outstanding
        party.opening_balance_as_of = snap.outstanding_as_of
        snap.kept_ours_outstanding = None
    else:
        raise DifferenceError("Choose Tally's figure or yours.")
    session.flush()

# Payments — party-ledger, bill-wise allocation

Status: **built, committed on `main`** (not yet deployed to prod as of
2026-09-05 — no evidence of a prod migration run for 0017/0018 in this
doc; confirm against the deploy log before assuming it's live).

## Why this shape, not a simpler one

The obvious naive design — a `payment` row per invoice — was rejected up
front: metal/scrap customers routinely pay one lump sum against several
outstanding bills at once, or pay less than one bill's total. So payments
are **party-scoped**, and each payment splits across N invoices via
`payment_allocation` rows — the same model Tally calls "bill-wise
allocation." This was a deliberate, discussed tradeoff (see "decisions
locked" below), not an oversight to revisit.

## Data model

- `payment` (migration `0017`): `party_id` (required — no invoice-less
  advance intake yet, see Pending), `date`, `amount`, `mode`
  (cash/upi/bank/cheque), `ref_no`, `notes`, `voucher_no` (gap-free
  per-tenant, via the existing `NumberSequence` table with
  `series="Payment"`, `fy="ALL"` sentinel — reused rather than inventing a
  new sequence mechanism), `ledger_name` (defaults from mode: Cash/Bank —
  **unused by any app logic today**, pure Tally-export scaffolding), `status`
  (posted/reversed).
- `payment_allocation`: `payment_id`, `invoice_id` (null for on-account),
  `type` (`against_invoice` | `on_account`), `amount`.
- `invoice_line.discount_pct` (migration `0018`, nullable `Numeric(5,2)`):
  a **persisted UI hint only** — the original % an operator typed, kept
  alongside the billing-authoritative `discount` (₹). `domain/tax.py` never
  reads it. Exists purely so a %-discount line round-trips as the same %
  after save/reload instead of silently converting to a ₹ figure forever.

## Decisions locked (don't re-litigate without re-reading why)

- **Overpayment → on-account credit.** Not blocked, not refunded. The
  server auto-creates the on_account remainder allocation if a client's
  allocations sum to less than the payment amount — the client never
  computes or sends it.
- **Advance payments (zero invoices) are explicitly deferred**, not
  forgotten. `payment.party_id` is required; there's no "pure advance,
  nothing to allocate against" entry point yet. The on-account mechanism
  above means the data model already supports this when that entry point
  gets built — see Pending.
- **Finalized invoices stay immutable** (pre-existing app-wide rule,
  reconfirmed for this feature) — `balance_due` is always
  `grand_total − paid`, no recompute-drift risk.
- **Reversal is a status flip, not a delete.** `payment.status='reversed'`
  + `reversed_at`/`reversed_reason`; `payment_allocation` rows stay in
  place for audit trail. Every balance-computing query filters
  `Payment.status == posted`, so a reversed payment's allocations stop
  counting automatically — this is the only mechanism, don't add a second
  "exclude reversed" branch anywhere new.
- **One shared `PaymentDialog` component, three launch points**
  (Collections row, Party Account tab, invoice balance strip) — never
  three separate dialogs. Pre-scoped by props (`partyId` always set,
  `focusInvoiceId` optional). It's a modal over the current screen; it
  never navigates away — recording a payment from an invoice keeps you on
  that invoice, the balance strip just updates in place.
- **Collections is the primary entry point**, not the full Parties list —
  built specifically because "find who to collect from" in a huge Parties
  list was the original complaint. It shows only parties with a non-zero
  net balance (`GET /api/collections?scope=outstanding|overpaid|either`),
  never the full party table.
- **IDOR-paranoid by design**: every allocation is re-validated inside the
  write transaction — invoice must belong to the *same party and tenant*,
  must be `status=final`, and the allocation amount is checked against the
  invoice's **live** `balance_due` recomputed inside the transaction (never
  a client-sent balance), with a Postgres row lock (`SELECT ... FOR
  UPDATE`) on the invoice to close the concurrent-double-allocation race.
  **Gotcha already hit once**: `Invoice.party` is `lazy="joined"`, so a
  bare `select(Invoice)` always outer-joins `party` — Postgres refuses
  `FOR UPDATE` on the nullable side of an outer join. The lock query uses
  `.options(lazyload(Invoice.party))` to drop that join. If you add a new
  row-locked `Invoice` query anywhere, you need the same `lazyload`.
- **Tally-export shape, not a Tally export.** `voucher_no` and
  `ledger_name` exist so a future export is a serialization, not a
  redesign — `against_invoice`/`on_account` map directly to Tally's "Agst
  Ref"/"New Ref" bill-wise-detail types. No export code exists yet.

## What's built

Backend (`api/`):
- `models/payment.py`, `services/payments.py` (balance math, gap-free
  voucher numbering, `collections_summary` — one aggregate query,
  party-driven `LEFT JOIN`s so an overpaid party with **zero** open
  invoices still surfaces, not an `INNER JOIN` that structurally can't see
  them), `routers/payments.py` (create/get/reverse, `/api/collections`,
  `/api/parties/{id}/ledger`), `/api/parties/{id}/open-invoices`.
- Invoice detail + list responses carry `paid_amount`/`balance_due`/
  `payment_status` (null unless `status=final`; list endpoint computes
  this via one aggregate join, not per-row N+1).
- Finalize gate hardened: blocks a line with `quantity<=0` or
  `unit_rate<=0` (quantity/rate were already blocked, confirmed and
  regression-tested), and blocks a **closed weighment segment with
  kg-bearing lines but a recorded weight of 0** — a piece-only segment
  with zero weight is correctly not blocked (`is_weight_uom` check against
  the segment's lines).
- PDF (`invoice_v1_nongst.html` + `pdf.py`): prints "Amount Received" /
  bold "Balance Due" under Grand Total whenever `paid_amount > 0` for a
  finalized invoice — works for a payment recorded before finalize (the
  editor's Partial/Full option) or any time after (re-render picks up the
  latest state). A bill with zero payments prints exactly as before (no
  block shown).
- 14 payment tests + 8 finalize-guard tests + 4 PDF-context tests, full
  suite green, no regressions across the arc.

Frontend (`web/`):
- `PaymentDialog.tsx` — allocation table is **derived** (`useMemo` over
  the open-invoices query + typed amount + a manual-edit map), not
  stored/re-seeded via competing `useEffect`s — that was a real shipped
  bug (visible flicker while typing the amount for the first few
  keystrokes) fixed mid-arc. FIFO-suggested, every row editable. "Pay in
  full" quick-fill button. On-account remainder callout when allocated <
  amount.
- `CollectionsPage.tsx` — new nav item, scope chips (Owes us / Overpaid /
  Either), balance/oldest sort, overpaid rows shown as a green credit not
  a red debt.
- `PartyAccountTab.tsx` + a real 2-tab strip (Details/Account) on the
  Parties detail page — this app had **no tab pattern before this
  feature**; it's now the one precedent if another tab gets added later.
  Credit balance also shown correctly (was a display bug: negative
  `running_balance` briefly rendered as "−₹X outstanding", fixed).
- Invoice editor: balance strip + Record-payment button (finalized only);
  a **3-way finalize-time control** — No payment / Paid in full / Partial
  (typed amount, clamped to grand total) — plus a **live totals-rail
  preview** ("Payment on finalize" / "Balance after payment") with an
  explicit note that Save Draft does **not** persist this choice (only
  Finalize records the payment) — this distinction was a specific ask,
  don't let the UI go quiet about it again.
- Unit-string normalization (`normalizeUom`: pcs/pc/piece/no/each→nos,
  kgs→kg) and quantity-decimal trimming (`trimQty`: backend's "1.000"
  reads as "1", matching a freshly-typed line) — fixed as a byproduct of
  the payment UI review surfacing pre-existing invoice-line display bugs.
- Weighment zero-guard: both the "Close weighment" dialog and the
  editable slip-divider input now block/flag a blank-or-zero recorded
  weight when the segment actually has kg-bearing lines.

## Pending / explicitly not built

- **Advance payment intake** (payment with zero invoices, pure on-account
  from day one) — deferred per the locked decision above. Data model
  already supports it (on_account allocation type exists); needs its own
  entry point when prioritized.
- **Tally export** — schema is shaped for it, no export code exists.
- **Payment editing** — a wrong payment is reversed (status flip), never
  edited in place. No UI/API for "correct this payment's amount/mode" —
  only reverse-and-re-enter.
- **Deploy**: confirm 0017 + 0018 have actually run against prod before
  assuming this is live for real users — this doc was written from local
  dev/test state, not a deploy log.
- **Rate/finalize sanity ranges** (an explicitly declined broader option
  during this arc): only hard zero/invalid blocks were built — no
  "rate looks 10x the usual band" or similar soft warnings beyond what
  the price-band guard already did pre-existing this feature.

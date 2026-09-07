# Payments — party-ledger, bill-wise allocation

Status: **built, committed on `main`** (7 commits `27ba3ad`→`bbf919c`,
2026-09-04→06). **Not confirmed deployed** as of 2026-09-06 — no evidence
of a prod migration run for 0017/0018/0019 in this doc; confirm against
the deploy log before assuming it's live.

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
- `payment_allocation.invoice_id` FK is **`ON DELETE SET NULL`** (migration
  `0019`, added after 0017 shipped it as a bare unconstrained FK). See
  "Delete guard" below for why.

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
- **Deleting an invoice with a payment: blocks only on a `posted` payment,
  not a `reversed` one.** A reversed payment's allocation row is kept
  forever (audit trail, per the reversal decision above) — if "any
  allocation row exists" were the gate, a reversed payment would
  *permanently* pin its invoice, making "reverse it first" a dead end.
  `payment_allocation.invoice_id` is `ON DELETE SET NULL` (migration
  `0019`) specifically so the delete can go through once only reversed
  allocations remain — the payment record itself (reversed status
  included) is never touched by deleting its invoice.
  **NOTE (2026-09-07): this `posted`/`reversed` distinction now only
  matters for a *draft* or a legacy *cancelled* invoice. A `final`
  invoice can no longer be cancelled or deleted at all (see the invoice
  feature notes / EXECUTION-PLAN §8), so "reverse the payment then delete
  the invoice" is no longer a path for a numbered invoice — the delete
  still 409s on `status == final` regardless of payment state.**

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
- **`delete_invoice` guard** (`routers/invoices.py`): checks for a
  `posted` `payment_allocation` against the invoice and refuses with a
  clean 409 ("reverse it first") instead of letting the DELETE reach
  Postgres and fail as a raw FK-violation. Only checks `posted` — see the
  "decisions locked" entry above for why `reversed` doesn't block.
- 15 payment tests + 17 finalize-related tests (`test_invoice_finalize.py`,
  includes both the pre-existing gate and the 8 new zero/weighment guards)
  + 4 PDF-context tests, full suite green throughout, no regressions
  across the whole arc.

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
  **Each non-reversed payment row now has a "Reverse" action** — prompts
  for a required reason, calls `POST /payments/{id}/reverse`. Before this
  was added there was genuinely no way to reverse a payment from the UI at
  all (only the API existed) — worth remembering if a future session sees
  "reverse it" advice anywhere and goes looking for the button.
- Invoice editor: balance strip + Record-payment button (finalized only);
  a **3-way finalize-time control** — No payment / Paid in full / Partial
  (typed amount, clamped to grand total) — plus a **live totals-rail
  preview** ("Payment on finalize" / "Balance after payment") with an
  explicit note that Save Draft does **not** persist this choice (only
  Finalize records the payment) — this distinction was a specific ask,
  don't let the UI go quiet about it again.
  **The finalize-time payment call also re-renders the PDF afterward.**
  `finalize_invoice()` renders the PDF as its own step, synchronously,
  *before* the frontend's follow-up payment POST is even sent — so
  without an explicit re-render call after that payment succeeds, the
  very first PDF a "pay at finalize" invoice ever got was permanently
  stale (no Amount Received / Balance Due lines) until someone happened to
  click "Re-render PDF" by hand. Fixed, best-effort (a failed re-render
  here doesn't fail the whole finalize+pay action).
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
- **Party opening balance** (a balance carried from before go-live) — the
  ledger and every balance-computing query start at zero and see only
  in-system invoices/payments, so a pre-existing balance is invisible.
  Design agreed 2026-09-07 (editable manual figure on `party`, locks once
  the party has any invoice/payment) but **nothing built** — full plan in
  `docs/FEATURE-party-opening-balance.md`, visual review in
  `docs/visual-plan/party-opening-balance-review.html`. This is what makes
  "show the party's balance when creating a new invoice" actually correct.
- **Tally export** — schema is shaped for it, no export code exists.
- **Payment editing** — a wrong payment is reversed (status flip; the
  frontend action IS built now, see "What's built"), never edited in
  place. No UI/API for "correct this payment's amount/mode" — only
  reverse-and-re-enter.
- **PDF staleness for a payment recorded well after finalize.** The
  finalize-time payment path now auto-reruns the PDF (fixed, see "What's
  built"), but a payment recorded later — from Collections, the Account
  tab, or the invoice balance strip, anytime after finalize — does NOT
  trigger a re-render. `GET /invoices/{id}/pdf` just serves whatever file
  is already on disk with no staleness check; the operator has to know to
  click "Re-render PDF" by hand. This is the same underlying issue as the
  finalize-time bug, just not closed for every entry point — a real gap,
  flagged but not fixed.
- **Deploy**: confirm 0017 + 0018 + 0019 have actually run against prod
  before assuming this is live for real users — this doc was written from
  local dev/test state, not a deploy log.
- **Rate/finalize sanity ranges** (an explicitly declined broader option
  during this arc): only hard zero/invalid blocks were built — no
  "rate looks 10x the usual band" or similar soft warnings beyond what
  the price-band guard already did pre-existing this feature.
- **SQLite test suite doesn't enforce foreign keys** (`PRAGMA
  foreign_keys` is off, nothing turns it on) — no test in this project can
  verify `ON DELETE SET NULL` (migration 0019) actually fires at the
  database level; only the application-layer delete guard is
  test-covered. The migration's SQL was hand-verified by executing it
  directly against a live SQLite connection outside the test harness (not
  via `alembic upgrade`, which fails on an earlier Postgres-only
  migration when run against SQLite) — real confidence needs an actual
  Postgres run.

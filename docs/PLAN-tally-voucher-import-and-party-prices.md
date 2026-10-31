# Plan: Tally voucher import (full) + party price history

Status: **P0 raw capture BUILT 2026-10-28, uncommitted, not yet run against any Tally.** Everything else is plan only. Alembic head `0062` (P0 needs no migration).

**Party price history (section 6) BUILT 2026-10-28, uncommitted, typecheck/lint clean, backend tests pass, NOT yet clicked through in a browser:**
`api/app/services/invoices/price_history.py`, routes `GET /api/invoices/price-history` and
`/price-history/last` (registered before `/{invoice_id}`), tests `api/tests/test_invoice_price_history.py` (5);
web: `lib/priceHistory.ts`, `components/PriceHistoryPanel.tsx`, and `InvoiceEditorPage.tsx`
(party-price prefill that replaces only an item-filled rate, caption with a "N months ago" note
at 6+ months, Alt+S panel plus a button, "Use their last rates" banner when the party changes
after lines are priced, unit-mismatch not filled). Voice and photo lines get the caption but are
not prefilled from the party price. Imported Tally sales will feed it unchanged once the
voucher import writes native invoice rows (`source` is already in the response).

P0 pieces: `api/app/services/tally/voucher_capture.py` (request builders, Export-only guard, job
enqueue, result handler), `api/tools/tally_capture.py` (operator CLI: `vouchers`, `bills`, `xml`,
`fetch`), `pull_vouchers` result branch in `routers/tally_agent.py`, `case "pull_vouchers"` in
`TallyMastersModule.cs`, tests `api/tests/test_voucher_capture.py` (9) and
`tally-agent/TallyAgent.Tests/VoucherCaptureTests.cs` (7). **Request shapes VERIFIED on the dev
TallyPrime 7.1 (2026-10-28):** the collection export with a named-field `FETCH` returns vouchers
with item lines, ledger lines and bill allocations (`<FETCH>*.*</FETCH>` returns headers only, and
fetching every nested method is ~6x bigger: use the named list); `Bills Receivable` returns
`BILLFIXED` (date, ref, party) + `BILLCL` + `BILLDUE` + `BILLOVERDUE`. Real responses saved as
`api/tests/fixtures/tally_vouchers_dev_sample.xml` and `tally_bills_receivable_dev_sample.xml`.
**Parser traps seen:** rate is text `96.00/Nos`, quantity `10 Nos`; ledger amounts are signed
(party debit is NEGATIVE, `ISDEEMEDPOSITIVE=Yes`); bill dates are `2-Sep-26` but voucher dates
`20260902`; our own pushed voucher shows bill type `On Account` with no reference; dev data has
no Receipt vouchers, so receipt bill-wise allocation still needs the pilot. Size ~10 KB a voucher.
They are built server-side, so a corrected shape needs no agent release,
and `tools.tally_capture xml --file` sends any hand-written Export. Needs agent release 1.0.2
(tag, sign, canary-pin the pilot, promote) before the pilot can run it.
Test-run gotcha: a parallel session shares `_mytest.db`; use a private `DATABASE_URL` file.
Decided by the user: build the voucher import **whole, not in pieces**; collections and
payment reminders are to sit on top of it. Price history is the first consumer.

## 1. Why one plan

Two asks turned out to need the same data:

1. **Party price history in the invoice editor** (last price this party paid for this item,
   a history key aligned with Tally's Alt+S Stock Query, prefill with a caveat).
2. **Collections / reminders that are right from day 1 of onboarding**, and stay right when a
   receipt is entered in Tally and not here.

Today the Tally import is **masters only** (`pull_masters`, report `List of Accounts`:
ledgers, items, groups, opening balances). Voucher traffic is **push only** (`push_sales`,
`push_purchase`, `push_items`). A new shop therefore has no per-party prices and no ledger
history until its first finalized bills, and any receipt keyed in Tally never reaches ageing
or reminders (`MASTER-tally-complement-saas.md` section F-push notes: "Future: voucher-pull slice").
This plan is that slice.

## 2. The central design decision: imported vouchers become native rows

The whole collections stack reads three things: finalized `invoice` rows, `payment` +
`payment_allocation` rows, and `party.opening_balance`
(`services/payments.py`: `balance_due_for_invoice`, `outstanding_balance_for_party`,
`ageing_summary`, `party_ledger`; `services/statements.py`; `services/reminders.py`).

So the import writes **into those tables**, marked as imported, instead of a side table.
Then ageing, statements, reminder proposals, the Collections page, the "previous outstanding"
PDF block and price history all work with no second code path.

(Earlier in this thread I suggested a separate `tally_sales_history` table. That was right for
prices alone and wrong for collections. A side table would mean every collections query
reads two sources. Rejected.)

Cost of this choice, and how it is contained:

| Risk | Containment |
|---|---|
| Imported invoice gets pushed back to Tally (duplicate voucher) | `invoice.source = 'tally_import'` blocks Push to Tally, finalize, cancel, edit, PDF render, WhatsApp invoice send. Guard in one place (`push_readiness.py` plus the invoice router guards). |
| Imported numbers collide with our gap-free numbering | Imported invoices keep the **Tally voucher number** as text and live in their own `series` (for example `TallyImport`); they never take a number from the native sequence. Their integer `number` stays empty (the unique constraint ignores nulls on Postgres). Our sequence can instead be seeded to continue the shop's Tally numbers; see Q1 in section 9. |
| Our own pushed vouchers come back on the next pull and double up | Pushed vouchers carry the UDF `METALERP_REF` (`services/invoices/tally_xml.py:139`) and `TallyLink.last_pushed_at` exists. The import skips any voucher whose `METALERP_REF` or Tally GUID already maps to a native row, and links it instead. |
| Tally edits or deletes a voucher after import | Imported rows are **Tally-owned**: every pull re-checks them by checksum (`TallyLink.checksum`) and updates, or marks cancelled when Tally says cancelled or deleted. Never hand-editable here. |
| `opening_balance` locks once history exists (`has_ledger_history`) | The import sets `opening_balance` and `opening_balance_as_of` first, in the same transaction, then adds history (section 4). |

## 3. What gets pulled (Tally side)

One agent action, `pull_vouchers`, mirroring `pull_masters` (job kind, in-flight guard,
staging, result callback). Gateway export per month-sized chunk (a year of a busy shop in one
call is too big) of the Voucher/Day Book collection, with inventory entries and bill
allocations included.

| Voucher type | Becomes | Needed for |
|---|---|---|
| Sales | `invoice` (final, source tally_import) + `invoice_line` (item, qty, unit, rate, discount) | Prices, ageing, statements |
| Receipt | `payment` + `payment_allocation` | Ageing, reminders stop when paid in Tally |
| Credit Note | `invoice` with `doc_type = crn` | Net balances |
| Journal / Debit Note | staged and counted, applied only if it touches a customer ledger | Balance correctness (write-offs, discount adjustments) |
| Purchase / Payment | **staged only** this release (supplier side) | Later AP; the pull fetches them anyway so no second pull is needed |
| Contra, Stock Journal, etc. | ignored, counted | |

**Bill-wise allocation is the key field.** Tally receipts carry `BILLALLOCATIONS`
(`New Ref`, `Agst Ref`, `Advance`, `On Account`). That maps one-to-one onto our
`against_invoice` / `on_account` allocation types, so receipts land on the right bill and
ageing buckets are correct instead of guessed.

**Pending bills as the backstop.** A second report, `Bills Receivable` (pending bills with
due dates), lists open bills of any age. Use it two ways:
1. bills older than the import window still enter as open invoices (no history lines),
   so a 12-month window does not hide a 20-month-old unpaid bill;
2. **reconciliation check**: after import, per party, our computed outstanding must equal
   Tally's ledger closing balance. Differences are shown to the shop, not silently fixed.

Window: default **12 months** of vouchers plus all pending bills regardless of age. Shop can
extend. Opening position at the window start comes from Tally's ledger balance on that date
and is written to `party.opening_balance` + `opening_balance_as_of`.

## 4. Backend work

**Migration `0063`** (single migration for the whole feature):
- `invoice.source` (`native` default | `tally_import`), `invoice.tally_voucher_no` (text),
  `invoice.series` use for imported series
- `payment.source`, `payment.tally_voucher_no`
- `tally_voucher_stage` (like `tally_import` / `tally_stock_import`): raw parsed voucher JSON,
  batch id, status (`new` | `matched` | `unmatched` | `applied` | `skipped`), reason
- `TallyLink.entity_type` already allows `invoice` and `payment`; no change
- index for price lookup: `invoice_line (item_id)` exists; add `invoice (tenant_id, party_id, status, date desc)` if the plan shows it is needed

**Services:**
- `services/tally/voucher_xml.py` parse (sales, receipt, credit note, journal) with fixtures from
  a real pilot export
- `services/tally/voucher_import.py`: stage, resolve party (by `tally_guid`, falling back to
  ledger name) and item (by `tally_guid`, then name via the existing resolve ladder, never
  auto-creating items without review), apply in a transaction per batch with a per-voucher
  savepoint (pattern already used in bulk ops)
- `services/tally/voucher_reconcile.py`: per-party outstanding vs Tally ledger balance
- `services/tally/jobs.py`: `enqueue_pull_vouchers`, same in-flight guard as masters
- guards on every write path for `source = tally_import`

**Agent (C#):** `TallyMastersModule` gets `case "pull_vouchers"`, chunked month requests,
result POST in pages (the result envelope must not exceed the API body limit; same approach
as large masters). **Note:** agent releases are signed and auto-updating, so this ships
through the normal release flow.

**Routes** (Replicate-Fleek rule: this is Metal ERP-internal, no Fleek counterpart; still
recorded here for review): `POST /api/tally/pull-vouchers`, `GET /api/tally/voucher-batches`,
`GET /api/tally/voucher-batches/{id}` (review: matched, unmatched, reconciliation),
`POST /api/tally/voucher-batches/{id}/commit`.

## 5. Onboarding flow (web)

After the masters import commit, a second step on the same screen:
"Import sales and receipts from Tally" with period picker (default last 12 months) and
progress. The review screen shows counts (vouchers, parties matched, items matched),
unmatched ledgers/items to fix, and the **reconciliation table** (party, ours, Tally's,
difference). Commit applies. Re-running later is **incremental**: new vouchers by date plus
altered vouchers by checksum.

## 6. Price history (consumer 1)

Reads `invoice_line` joined to `invoice` where `status = final`, `doc_type = inv`, for this
party and item, so imported sales count automatically with no extra table. Cancelled
invoices, credit notes and drafts are excluded.

- `GET /api/invoices/price-history?party_id=&item_id=&limit=` returns `{last, history[]}`,
  each row labelled `native` or `tally` so the shop can see where a price came from.
- **Prefill order:** party's last sale, then `item.last_rate`, then `standard_rate` /
  `default_rate`. A hand-typed rate is never overwritten. If the last sale is old
  (fixed 6-month cutoff, no further staleness logic by decision), still prefill and show
  a caveat in the hint: `last to Sharma Rs420/kg, 12 Apr (6+ months ago)`.
- **History key: Alt+S**, to match Tally's Stock Query. Opens a popover with the last 5 to 10
  sales (date, voucher no, qty, unit, rate, discount). Click applies the rate. Intercept the
  key on the rate field (Firefox/Windows binds Alt+S to its History menu). A history button on
  mobile.
- Party changed after lines exist: prompt "Refresh rates for <party>?", never silent.
- Unit mismatch (kg history, piece item): show history, do not prefill.
- Voice and photo capture flows call the same lookup.
- `finalize.py:224-230` keeps maintaining `item.last_rate` as the fallback tier.
- Imported lines with an unmatched item have `item_id` null and are invisible to price
  history until matched in the review screen; counted in the batch summary.

## 7. Collections and reminders on top (consumer 2)

Nothing new is built for these if section 2 holds. What changes:
- Ageing and statements now include Tally-origin invoices and receipts from day 1.
- Reminder proposals stop for a party whose receipt was entered in Tally once the next pull
  runs. So **freshness matters**: add a scheduled incremental pull (daily, per shop, opt-in)
  and a "last synced from Tally" stamp on the Collections page so the shop knows how current
  the ageing is. The scheduler needs the existing reminder-job infrastructure, no new infra.
- Reminder messages and statements must show imported invoices correctly: voucher number text
  instead of our numbering, no PDF link (imported invoices have no PDF here).
- Promise-to-pay (F3d) is unaffected and can come after.

## 8. Phases (one release, built in this order, nothing user-visible until the last)

1. **P0 spike on the pilot Tally:** capture real Gateway XML for Sales, Receipt, Credit Note,
   Journal, Bills Receivable; measure size and time per month on a busy company. Output:
   fixtures and a go/no-go on chunk size. *Blocks everything else.* The pilot shop is already
   connected through the agent, so no manual export is needed, but the agent only knows fixed
   actions (`pull_masters`, `push_*`), so P0 ships a **raw-capture `pull_vouchers`** first:
   it sends the request to the Gateway, uploads the unparsed XML to R2 (same path as masters)
   and reports size and elapsed time. Request shapes are designed beforehand against the dev
   Tally at `localhost:9000`. This is the first slice of P1, so nothing is thrown away.
2. **P1 data + agent:** migration `0063`, `pull_vouchers` action, staging, parser tests.
3. **P2 apply + guards + reconcile:** party/item resolve, native-row write, dedupe vs pushed
   vouchers, immutability guards, reconciliation report.
4. **P3 onboarding screens + incremental/scheduled pull.**
5. **P4 price history** (backend lookup, prefill, caveat hint, Alt+S popover, mobile button,
   party-change prompt, voice/photo flows). Can be built in parallel with P3, ships with it.
6. **P5 collections surfaces:** "last synced" stamp, statement/reminder wording for imported
   invoices.

Verification: unit tests per parser/guard; fixture-based import test; an end-to-end on the
pilot (licensed) Tally. **The dev TallyPrime is unlicensed** (voucher dates restricted to the
1st or 2nd of a month), so it cannot exercise a realistic date spread; only the pilot can.

## 9. Decisions (user, 2026-10-28) and what is still open

- **Q1 Numbering: DECIDED in principle, one check left.** Imported invoices keep Tally's own
  voucher number as text (`tally_voucher_no`, shown on screen) and never take a number from our
  native gap-free sequence, so a Tally "142" and our "142" can never be confused or collide
  in the database. **Continuity, so the shop sees one series:** our push sends our integer as
  `VOUCHERNUMBER` (`invoices/tally_xml.py:129`), so on a Tally that already has Sales 1..N our
  invoice 1 would be a duplicate voucher number there. At import, read the highest Tally Sales
  voucher number; if it is purely numeric, offer to **seed our `NumberSequence`
  (`models/common.py:50`) to N+1**. If Tally numbers are prefixed text (`SL/24-25/0091`) we can
  only continue the counter, not the format; flag it in the review screen. Still to verify:
  whether `NumberSequence` can be seeded safely mid-year (P0 task).
- **Q2 Purchase side: staged only** this release (unchanged), pulled so no second pull is needed.
- **Q3 Window: DECIDED.** Default 12 months, **configurable** in the period picker, and
  stated plainly at import: "Importing sales and receipts from <date> to today. Open bills of
  any age are always included. Prices and history before <date> will not be available."
  Re-running with an earlier start extends history additively; the opening balance moves back
  to the new start (allowed only while every row before it is imported, not native).
- **Q4 Opening balances: DECIDED.** Today they are typed by hand only (Quick-Create-Party
  dialog, party form, and the inline field under Bill to on the invoice editor); the masters
  import does not bring them (no opening-balance handling in `tally/staging.py` or
  `parties_import.py`). So the voucher import sets `opening_balance` only where the party has
  no ledger history. Where a hand-typed figure exists and differs from Tally's, show the
  difference and keep the hand-typed value unless the shop accepts Tally's.
- **Q5 Scheduled pull: DECIDED.** Both: daily (opt-in per shop) and an on-demand button, same job.
- **Q6 Duplicates: DECIDED, shop reviews.** Two different things, handled differently:
  1. *We already hold it* (a voucher we pushed, or one imported earlier): matched
     automatically by `METALERP_REF`, Tally GUID or checksum. No review, never shown as a
     duplicate.
  2. *Genuine double entry* (the same bill keyed in Tally and here without a push, so there is
     no link): candidates are found by same party, same date, same amount, and are **held
     out of balances and prices until the shop decides** "same bill" (link, keep ours, skip the
     import) or "different". Tally itself can hold repeated voucher numbers (a voucher type
     with manual numbering may allow duplicates), so the voucher number alone is not a
     safe match key.

## 9a. Telling imported vouchers apart

Yes, three origins are distinguishable and shown:
- **Native**: made here (optionally pushed; linked by `TallyLink`).
- **Tally import**: `source = tally_import`, with Tally's voucher type name kept
  (`Sales`, custom types such as `Sales - Cash` or `Export Sales` come in under their parent
  type), the Tally voucher number and GUID.
- A "Tally" badge on the invoice list, detail and ledger rows, plus a source filter, and the
  price-history popover labels each row native or Tally.

## 10. Not in scope

GST ledgers and tax split from vouchers (GST billing is set aside), stock reports and
inventory movement (Tally stays the stock truth), voucher editing here, supplier-side
reconciliation, payment-gateway links.

## Status (2026-10-31)

P0 raw capture built (agent action in commit 62f0b00, NOT in released agent 1.0.2). P1 parser BUILT, uncommitted: `services/tally/voucher_xml.py` (`parse_vouchers`, `parse_bills_receivable`, `classify`) + `tests/test_voucher_xml.py` (11 pass, real TallyPrime 7.1 dev captures; the fixture has Sales/Purchase/Journal only, Receipt and Credit Note shapes are from the S4.4 live probe, not a capture). Still to build: migration (next number is 0084, chains off 0083, which must be committed with it), `tally_voucher_stage`, `voucher_import.py` (resolve, apply, dedupe vs METALERP_REF), guards on `source=tally_import`, reconcile, routes, month chunking in the agent result path, review screen.

## Direction change (2026-10-31): refill, not history

User: do not import past invoices now; refill the parties and items already imported. Four things: party outstanding (Bills Receivable/Payable) written to `party.opening_balance` + `opening_balance_as_of` where the party has no history, plus a "Tally says" snapshot each refresh overwrites; party last-purchase (date, amount, count); item stock on hand (Stock Summary, per company, name match only); last price per party+item (voucher pass reduced month by month, only the latest rate kept). Pilot Positive: treat as blank slate (its one invoice was a test, no handling). BUILT: `services/tally/stock_summary.py` + `tests/test_stock_summary.py` (real 920-row export, trimmed fixture). The Stock Summary Gateway REQUEST is unverified against a live Tally. Not built: snapshot tables, bills->opening balance writer, last-price reducer, routes, screen, agent release.

## Update 2026-10-31 (later): id-keyed light queries replace the name-keyed reports

Stock Summary and Bills Receivable carry names only, and a Gateway "Stock Summary" request returns a single rolled-up row. The refill now reads two light Collection queries instead (ledgers and stock items with Tally's id, closing figures), matches by the id the masters pull stored, and corrects a link's name when Tally renames. Verified live on the dev Tally. `stock_summary.py` and the Bills Receivable parser remain for UI exports and as a source of pending-bill dates.

# Party opening balance — editable, history-gated

Status: **BUILT 2026-09-07, uncommitted.** Full backend suite green
(374 passed / 1 skip); +4 payments tests, +3 parties-CRUD tests,
+3 PDF-context tests. Frontend `tsc`, eslint, `npm run build` all clean.
NOT deployed, NOT committed (user does check-ins). Migration `0021` applies
on the next `alembic upgrade head`. Visual review:
`docs/visual-plan/party-opening-balance-review.html`.

**Invoice PDF — "previous outstanding" block (added after first review):**
a finalized invoice's PDF now prints, below Grand Total, when the party
owes anything else:

```
Previous outstanding (before this bill)   10,000.00
Add: This invoice                          8,919.00
Total amount due                          18,919.00
```

`previous_outstanding_for_party(session, party_id, exclude_invoice_id=...)`
= opening balance + Σ balance_due of the party's *other* finalized invoices
− on-account credit. Omitted entirely when that is 0 (never printed as
"0.00"). Uses each other invoice's *live* balance_due so a later payment on
an earlier bill reduces the figure on a re-render. Sign follows the ledger
(a net-credit party shows negative). Wired in `services/invoices/pdf.py`
+ `templates/invoice_v1_nongst.html`; the editor has no A4 preview so no FE
change there.

Editable from invoice entry for a new client via **both** entry points —
(a) fields in the invoice picker's Quick-Create-Party dialog, and (b) an
inline editable "Opening balance" field on the invoice body under "Bill to",
editable while the selected party has zero ledger history and read-only
(the prior-balance line) once it locks.

The plan below is what was built — file paths and behaviour match the code.

## The gap

Creating an invoice for an existing party shows nothing about what that
party already owes. Deeper: there is **no opening-balance concept for
parties anywhere in Metal-ERP** — `party_ledger()`
(`api/app/services/payments.py`) starts its running balance at zero and
walks only finalized invoices + payments, so any balance carried from
before go-live is invisible. `outstanding_balance_for_party()` and the
Collections aggregate have the same blind spot.

## Decided shape

- **Source: manual entry, NOT Tally.** The operator types the figure. The
  Tally party importer is *not* touched (it currently discards ledger
  `OPENINGBALANCE` and stays that way).
- **`party.opening_balance` NUMERIC(14,2) NOT NULL DEFAULT 0** +
  **`party.opening_balance_as_of` DATE NULL** (optional; shown as the
  statement's first-line date).
- **Editable only while the party has zero ledger history** — no
  finalized invoices, no payments (reversed payments count as history
  too: a reversed payment still means the party was transacted). Draft
  invoices do **not** lock it.
- Once locked: field is read-only in the UI (disabled + 🔒 note) and the
  server returns **409** on a `PATCH` that changes it.
- **Sign convention matches the ledger: positive = party owes you**
  (debit). A negative opening balance = customer advance → renders as a
  credit, card flips to green "Credit balance" exactly like an unapplied
  payment does today. Negative values are allowed.

## Plan (per file)

### Backend

1. **Migration `0021_party_opening_balance.py`** — add the two columns
   (`server_default='0'` for `opening_balance`); downgrade drops both.
   Alembic head becomes `0021`, down_revision `0020`.
2. **`api/app/models/party.py`** — two `mapped_column`s (`Decimal`,
   `date | None`).
3. **`api/app/services/payments.py`**
   - New `has_ledger_history(session, party_id) -> bool` — `EXISTS` a
     finalized invoice OR any `payment` row (any status).
   - `party_ledger()` — seed `running = party.opening_balance`; if
     non-zero, prepend a synthetic
     `LedgerEntry(kind="opening", date=opening_balance_as_of or
     party.created_at.date(), ref_id=party.id, ref_label="Opening
     balance", debit=ob if ob>0 else 0, credit=-ob if ob<0 else 0,
     running_balance=ob, status="opening")`. Sorts before every real
     event (tie index -1). No `Reverse` action on it in the UI.
   - `outstanding_balance_for_party()` — add `+ party.opening_balance`.
   - `list_party_balances()` (the Collections one-round-trip aggregate,
     ~line 126) — fold `opening_balance` into `net_balance_expr`; adjust
     the `> 0` / `!= 0` scope filters and the `== 0` (fully-settled)
     exclusion so a party whose only balance is an opening figure still
     appears in the `outstanding` scope.
4. **`api/app/schemas.py`** — `opening_balance` + `opening_balance_as_of`
   on `PartyBase`, `PartyOut` (plus derived `opening_balance_locked:
   bool`), `PartyUpdate`. Add `"opening"` to
   `schemas_payments.PartyLedgerEntry.kind` literal.
5. **`api/app/routers/parties.py`**
   - `_out()` — pass the three fields (`opening_balance_locked =
     has_ledger_history(...)`).
   - `create_party` — accepts as-is (new party = no history).
   - `update_party` — if `opening_balance` or `opening_balance_as_of` is
     in the patch **and** `has_ledger_history()` **and** the value
     actually changes → `409 "Opening balance is locked once the party
     has invoices or payments."`

### Frontend

6. **`web/src/lib/types.ts`** — the two fields + `opening_balance_locked`
   on `Party` **and on `PartyListItem`** (so the invoice picker knows
   whether to show the field editable without a second fetch — add
   `opening_balance` + `opening_balance_locked` to the `PartyListItem`
   schema in `api/app/schemas.py` too); `"opening"` on
   `PartyLedgerEntry["kind"]`.
7. **`web/src/components/PartyForm.tsx`** — "Opening balance" ₹-prefixed
   input + optional "As of date", in the identity grid under GSTIN /
   Default state. Autosaves like every other field. Disabled + 🔒 helper
   text when `opening_balance_locked`.
8. **`web/src/components/PartyAccountTab.tsx`** — render the
   `kind === "opening"` row (grey dot, italic "Opening balance" label, no
   Reverse button). The balance card already reads
   `entries[0].running_balance`, so it picks up the opening figure for
   free once the ledger seeds it.
9. **`web/src/pages/invoices/InvoiceEditorPage.tsx`** — the original ask,
   now **editable for a new client**. When a party is picked, read the
   ledger (reuse `GET /parties/{id}/ledger`, already cached under the
   `["party-ledger", id]` query key — no new endpoint) AND read
   `opening_balance` / `opening_balance_locked` off the party (the picker
   already returns a `PartyListItem`; add the two fields there, or fetch
   `GET /parties/{id}` on pick).
   - **`opening_balance_locked === false`** (new client, no
     invoices/payments) → render an **editable** "Opening balance
     (before this bill)" ₹ field + optional "as of" date, inline under
     "Bill to". Persist with `PATCH /parties/{id}` on blur (debounced,
     same pattern as `PartyForm`); a failed PATCH surfaces inline and
     does not block the invoice. Caption: *"↳ new client · no invoices
     yet"*.
   - **`opening_balance_locked === true`** → read-only balance line
     (deep-links to the Account tab):
     - opening only → *"Opening balance carried forward — ₹X · No
       invoices billed yet"*
     - running dues → *"Outstanding before this invoice — ₹X · incl. ₹Y
       opening · N open invoices"*
     - settled → neutral *"Outstanding before this invoice — ₹0 · All
       settled"*

10. **`QuickCreatePartyDialog` (in `InvoiceEditorPage.tsx`, ~line 1497)**
    — add optional "Opening balance" ₹ field + "as of" date to the "New
    party" mini-dialog. New parties always satisfy the lock rule, so the
    `POST /parties` body just carries `opening_balance` /
    `opening_balance_as_of` straight through. `PartyBase` already accepts
    them from step 4.

### Tests

- `payments.py`: ledger with opening balance only; opening + invoices +
  payments interleaved; negative opening (advance); `has_ledger_history`
  transitions (no history → draft invoice → finalized → payment).
- `parties` router: create with `opening_balance` (200, round-trips);
  set opening on a fresh party via PATCH (200); set after an invoice is
  finalized (409); GET / list return `opening_balance_locked`.
- Collections aggregate: a party whose only balance is an opening figure
  shows up in the `outstanding` scope.
- Editor flow (if a FE test runner ever lands — none exists project-wide
  today): field editable when `opening_balance_locked` false, read-only
  when true.

## What was built — decisions taken

1. **Lock trigger** = the party has a `final` invoice OR any `payment` row
   (posted or reversed). Draft/cancelled invoices don't lock.
   `has_ledger_history()` in `payments.py`.
2. **Editor reuses `GET /parties/{id}/ledger`** (cached under
   `["party-ledger", partyId]`) for the locked read-only line; no new
   `/balance` endpoint. It also does one `GET /parties/{id}` on pick to
   learn `opening_balance_locked` + current values (`PartyListItem` also
   carries `opening_balance` + `opening_balance_locked` for the picker).
3. **Negative opening balance allowed** — renders as a credit (green) in
   the Account tab, same as an unapplied payment.
4. **No-op PATCH of a locked field is allowed** (same value) so a generic
   "save the whole party form" still works; only a real change 409s.
   `PartyForm` further avoids the round-trip by omitting the opening
   fields from its PATCH body entirely when the party is locked.

## Implementation notes / deltas from the original plan

- The synthetic opening `LedgerEntry` uses `party.opening_balance_as_of`,
  falling back to `party.created_at.date()`, then `date.today()`. It's
  prepended before the sorted real events (not via a tie index) so after
  the display `reverse()` it lands last, matching the Account tab's
  newest-first order.
- `opening_balance_for_party()` was added to `payments.py` (a small helper
  reused by `outstanding_balance_for_party`).
- Pre-existing unused `datetime` import in `payments.py` removed while in
  the file.
- `NewPartyForm.tsx` (the standalone Parties-page create form) also got
  the fields, not just `PartyForm` — otherwise a party created there
  couldn't get an opening balance without a second edit.
- The invoice-editor block is a self-contained `PartyOpeningBlock`
  component near `PartyPicker`.

## Guardrails honoured

- **UoM display: `pcs`, never `nos`.** This feature is all currency (₹) and
  added no unit labels — nothing routed around `uomDisplay()`.
- Visual review done before UI coding; not committed / not pushed — user
  does check-ins.

## Sign convention — for the reviewer

**Positive = the party owes you.** An opening balance of ₹10,000 = "they
owed us ₹10,000 before this system started." It adds to what invoices then
pile on, so on the Account tab: opening `+₹10,000` → INV `+₹8,919` →
outstanding **₹18,919**. You enter a **negative** opening balance only for
a customer advance (you owe them) — it then shows as a green credit row.
This matches how invoice amounts add up; no double meaning.

## Not doing

- Tally import of opening balances (explicit: it does not come from Tally).
- Any "edit the opening balance later, with an audit trail" flow — once
  locked it's locked; a correction goes through a payment/adjustment
  entry instead (same philosophy as payment reversal, not editing).
- A live "previous outstanding" line in the on-screen invoice editor —
  there's no A4 preview component; the party's current balance is already
  shown by `PartyOpeningBlock` under "Bill to". The PDF block is enough.

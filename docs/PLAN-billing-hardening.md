# Billing hardening: roles, same-draft protection, payment reminders

Started 2026-10-07. GST billing is **out of scope for now** (user: "we will revisit GST"); `tenant.gst_enabled`
stays decorative and `finalize.py` keeps `v1-nongst`. Handwritten-bill capture and speech entry are also out.

## Decisions (user, 2026-10-07)

- **Roles: counter drafts, accountant finalizes.** Owner and accountant do everything. Counter creates and
  edits drafts, parties and orders, but cannot finalize, cancel, record or reverse payments, push to Tally or
  change settings. Viewer is read only.
- **Reminders: daily run, the shop reviews first.** The system proposes reminders each morning; the shop sends
  or skips them in one place. Off by default per firm.

## B1. Roles

| Gate | Roles | Used for |
|---|---|---|
| `WriteUser` (unchanged) | owner, accountant | finalize, cancel, payments, Tally, items, settings, share links |
| `DraftUser` (new) | owner, accountant, counter | invoice draft create / edit / delete / duplicate, party create / edit, order handling up to "Create draft invoice" |

- `counter` becomes assignable in the Ops console (`ASSIGNABLE_ROLES`).
- Web hides what the role cannot do (Finalize, Payments, Push to Tally, Settings) and says why, instead of
  letting the click fail. The server is the real gate.

## B2. Same-draft protection (optimistic lock)

- `invoice.created_by_user_id`, `invoice.last_edited_by_user_id` (migration 0052).
- `InvoiceUpdate.expected_updated_at` (optional, so old clients keep working). A mismatch returns
  `409 {code: "draft_changed", edited_by, edited_at}`; nothing is saved.
- The editor shows "Name changed this draft at HH:MM" with **Load their version** and **Keep mine and overwrite**.
- Drafts show "last edited by" so an accountant can see who prepared it.

## B3. Payment reminders

- `tenant.reminder_enabled` (default off), `tenant.reminder_days` (default `7,15,30`, days past due).
- Table `payment_reminder`: one proposal per party per day. Status `proposed | sent | skipped | failed`.
- Rule: an invoice is **due** after `invoice.date + default_credit_days`; it needs a reminder when its overdue
  days reach the next stage in `reminder_days` that has not been sent or skipped for it.
- **One message per party, not per invoice.** One invoice needs a reminder: `payment_reminder`
  (name, invoice number, balance due). Two or more: `account_statement` (name, total due, PDF).
- No party gets two reminders within 3 days of each other. Parties with no phone are listed as "no number".
- Runs hourly from the existing background loop in `main.py`, only after 09:00 India time, and is idempotent
  (a unique key per tenant, party and day), so a second worker or a restart cannot double-propose.
- Nothing is ever sent without the shop pressing Send.
- API: `GET /api/reminders`, `POST /api/reminders/run`, `POST /api/reminders/send`, `POST /api/reminders/skip`.
- UI: a "Reminders due" panel on the Collections page, and a settings block on the Firm page.

## Order of work

1. Backend B1, B2, B3 with tests (this file records what is built below).
2. Visual review page for the UI changes (`docs/visual-plan/billing-hardening-review.html`), then the web code.
3. User commits and deploys (migration 0052; take a backup first).

## Build notes

- **Backend built 2026-10-07, full suite passes** (migration `0052`). `DraftUser` gate in `deps.py`;
  `invoice.created_by_user_id` / `last_edited_by_user_id`; `InvoiceUpdate.expected_updated_at` + `409 draft_changed`
  (every save stamps `updated_at` itself, because a lines-only edit does not touch the invoice row);
  `services/reminders.py` + `routers/reminders.py` + `_reminder_loop` in `main.py` (hourly, from 09:00 IST, only proposes).
  `send_invoice(amount_override=)` so a reminder quotes the balance, not the bill total.
  Tests: `tests/test_billing_roles.py`, `tests/test_reminders.py`.
- **Automatic sending (user ask 2026-10-07), built:** `tenant.reminder_auto_send` (migration `0053`, default off). `auto_send` sends only
  proposals that crossed their stage within 3 days (`AUTO_FRESH_DAYS`), at most 40 per run, 09:00-19:00 IST only; older backlog stays on the
  review list so switching it on never blasts every long-overdue customer. A failed auto send is left for the shop, not retried.
- **Fleek's per-firm switch (user 2026-10-07), built:** `tenant.reminder_auto_allowed` (migration `0053`, default ON). Set in the Ops console
  (Firms page toggle "Automatic reminders allowed", the only web change so far). Off = the shop cannot choose automatic sending (403) and a
  firm that already had chosen it stops being sent to; proposals for review continue. Per firm only, no platform-wide switch.
- **Web screens BUILT 2026-10-07 (user approved the review page), type-check + lint clean, not yet exercised in a browser:**
  `components/RemindersPanel.tsx` (Collections, owner/accountant only), `components/ReminderSettingsCard.tsx` (Firm page; also edits
  `default_credit_days`, which was never exposed by the API before, so credit days were effectively always 0), draft-clash banner +
  "Prepared by" line + role hiding in `InvoiceEditorPage.tsx` (`lib/roles.ts`: `canWrite` / `canDraft`; counter loses Collections, Items, Firm
  in the nav and sees a disabled Finalize with the reason). Editor hydration now keeps a `baseline` (the `updated_at` it loaded) and, when a
  focus refetch brings a newer copy while there are unsaved edits, shows the banner instead of overwriting the screen.
- Firm page profile form now re-seeds only when the profile fields change (saving a settings card used to reset a half-typed profile).
- Also fixed 4 pre-existing web lint problems (ShareLinkDialog helpers moved to `shareLinkUtils.ts`, TallyDialog unused var).
- Note: a finalized invoice can never be cancelled or deleted, so a reminder can never point at a deleted invoice. The FK is
  `ON DELETE SET NULL` anyway.

# SCOPE — F3 Collections & Reminders · F4 Mobile Owner-Operated Billing

Status: **scoping only, nothing built** (2026-09-08). Written off the master
doc (`MASTER-tally-complement-saas.md` §F3/§F4) + a read of the current code.
Companion to it, not a replacement — this is the "what would we actually build,
in what order, against what already exists" pass.

**Visual review done 2026-09-09** — `docs/visual-plan/f3a-ageing-dashboard-review.html`
+ `docs/visual-plan/f3b-statement-whatsapp-review.html`. Decisions locked:

- **F3a buckets** = `<30 days / 30+ / 60+ / >3 months` (four, mutually exclusive;
  internal keys `lt30 / d30 / d60 / d90p`, labels display-only). Boundary:
  `age<=29 → lt30`, `30–59 → d30`, `60–89 → d60`, `>=90 → d90p`.
  Credit-days default **0**, tenant-wide only. Net total → caption under the
  strip. `<30 days` cell colour = neutral grey.
- **F3a Overdue** = worst bucket > 30 days → new scope chip, **radio-exclusive
  with Owes us / Overpaid / Either** (Owes us is a superset of Overdue, so
  selecting Overdue replaces it, doesn't stack). Plus a per-row **"Send
  statement"** link. `ageing_summary` returns `worst_bucket` + `is_overdue` so
  the chip needs no client scan.
- **F3b period** = preset dropdown: This month (default) / Last month / This FY
  (**Apr–Mar, hard-wired** — no tenant FY-start setting) / Last 90 days / Custom
  range. Endpoint takes `?period=&from=&to=`. Custom-range date fields render
  inline (native `<input type=date>`), same on phone + tablet.
- **F3b content** = full ledger for the window (invoices + payments +
  adjustments), NOT open-bills-only. First row is a synthetic
  *"Opening balance as on <start>"* = running balance carried forward from
  everything before the window. **Reversed payments in the window stay in**,
  struck-through with the reversal reason (never omitted). Closing line reads
  **"Total due ₹X"**.
- **F3b empty window** = no entries in range → **Send disabled** ("Nothing
  happened this period"); Download still works.
- **F3b file name** = `<Party slug> statement <YYYY-MM-DD>.pdf` via the existing
  `download_name()` helper — **today's date**, matching invoice PDFs, no period
  token.
- **F3b — NO migration.** `whatsapp_message` already has a nullable `party_id`
  FK (separate from `invoice_id`, see `models/whatsapp.py:51`). A statement row
  is `party_id` set / `invoice_id` null / `template_name="account_statement"`.
  Meta template `account_statement` params = `(party_name, total_due)`.
- **F3b entry points** = Party Account tab (Download + Send, both keep a plain
  Download) AND the F3a Collections per-row "Send statement" (opens the same
  send dialog, party pre-filled, period = This month). Bulk multi-select send
  (all overdue) stays **F3c**.

---

## TL;DR

- **F3** is ~70% already sitting in the codebase as primitives
  (`collections_summary`, `party_ledger`, `previous_outstanding_for_party`,
  the WhatsApp send path, `payment_reminder` already in `TEMPLATE_BODY_PARAMS`).
  What's missing is **ageing buckets** (today it's one flat "days since oldest"
  number), a **statement PDF + WA send**, and a **reminder scheduler + approval
  queue** — and there is **no scheduler infrastructure in the repo at all**,
  which is the one real new moving part.
- **F4** is **not a build, it's a dependency stub**. Everything user-facing
  exists (mobile invoice editor, quick-create party, payments, opening balance).
  F4 == "when F1 (Tally Connector) exists, enqueue a push job on finalize +
  show a sync chip." Until F1a/F1b land, F4 is 3–4 hours of chip UI + an
  enqueue call with nothing on the other end. **Recommend: do not start F4
  as its own slice — fold it into F1b.**

---

## Part A — F3 Collections & Reminders

### A.0 What already exists (don't rebuild)

| Primitive | Where | Does |
|---|---|---|
| `collections_summary()` | `api/app/services/payments.py:177` | one-query per-party net balance, scope `outstanding\|overpaid\|either`, sort `balance\|oldest`, `q` filter. Folds in `party.opening_balance`. Returns `oldest_unpaid_days`, `open_invoice_count`. |
| `party_ledger()` | `payments.py:326` | full chronological statement: opening row + invoices (debit) + payments (credit), running balance, newest-first. |
| `previous_outstanding_for_party()` | `payments.py:117` | opening + Σ other-invoice balance − on-account credit. Already used on the invoice PDF. |
| `GET /api/collections` | `routers/payments.py:254` | the list endpoint above. |
| `GET /api/parties/{id}/ledger` | `routers/payments.py:286` | the statement endpoint. |
| `CollectionsPage.tsx` | `web/src/pages/` | scope chips, search, balance/oldest sort, per-row `PaymentDialog` launch. Mobile-first, `max-w-2xl`. |
| `PartyAccountTab.tsx` | `web/src/components/` | per-party ledger table + Reverse action + credit-balance display. |
| WhatsApp send | `services/whatsapp.py` — `_send_template_message()`, `send_invoice()` | template send + PDF-as-document-header, per-firm `phone_number_id`, status webhook (`handle_status_webhook`), `WhatsappMessage` rows. |
| `payment_reminder` template | `whatsapp.py:49` — `TEMPLATE_BODY_PARAMS` | **already registered in code** as `(party_name, invoice_number, grand_total)`. **Not yet submitted to Meta for approval** — that's a lead-time item, do it first. |

**Gap vs. F3 spec:** no ageing *buckets* (0/30/60/90+), no statement *document*
(the ledger is JSON only, no PDF), no reminder *policy / schedule / approval
queue*, no *promise-to-pay*, and **no scheduler anywhere in the repo**
(`grep apscheduler|celery|cron` → nothing; only a manual `alias_sweep` CLI).

### A.1 Slice F3a — Ageing dashboard (read-only)

**Goal:** turn the flat "oldest 34d" into real buckets so an owner sees *shape*
of what's owed.

**Backend**
- New `ageing_summary(session, tenant_id, *, as_on=date.today(), q=None)` in
  `payments.py`, next to `collections_summary`. Per party:
  `current` (not yet due), `d1_30`, `d31_60`, `d61_90`, `d90_plus`, `total`,
  `oldest_bill_date`, `oldest_bill_number`, `last_payment_date`,
  `open_invoice_count`. Bucketing key = **invoice `date` + tenant credit-days**
  → due date; each invoice's *live* `balance_due` drops into the bucket its due
  date falls in. Opening balance → its own bucket by `opening_balance_as_of`
  (fall back to "current" if null).
  - Needs a per-tenant **credit period**. `Tenant` has no such field today —
    add `tenant.default_credit_days INT NOT NULL DEFAULT 0` (0 = "due on
    invoice date", the current implicit behaviour). Later: per-party override
    (`party.credit_days`), out of scope for F3a.
- One aggregate query, same shape discipline as `collections_summary`
  (party-driven LEFT JOINs — a party with only an opening balance still shows).
- `GET /api/collections/ageing?q=&as_on=` → `list[AgeingRow]`. New schema in
  `schemas_payments.py`.
- Keep `GET /api/collections` untouched — the dialog + existing page still use it.

**Frontend**
- Extend `CollectionsPage.tsx`: a bucket-total strip across the top
  (Current / 1–30 / 31–60 / 61–90 / 90+ with ₹ each, tap to filter the list),
  and per-row show the party's worst bucket as a coloured pill instead of
  today's "oldest Nd". Keep the existing scope chips + PaymentDialog launch.
- No new page — this is the same Collections screen, richer.
- Desktop: the `md:` table variant gets bucket columns.

**Migration:** `0022` (verify `alembic heads` first) — one column on `tenant`.

**Tests:** bucketing edge cases (invoice exactly on the boundary day; credit
days 0 vs 30; opening balance with/without `as_of`; a party with only a
reversed payment → no last_payment_date). Reuse the SQLite test DB pattern.

**Effort:** ~1–1.5 days. Low risk — additive, no write paths.

### A.2 Slice F3b — Statement → WhatsApp

**Goal:** one tap on a party → PDF of the account ledger for a chosen period
(opening balance → entries → closing) → send on WhatsApp (or download).
Decisions from the 2026-09-09 review are folded in below.

**Backend**
- New `render_party_statement_pdf(session, party_id, *, period, dt_from=None,
  dt_to=None)` in a new `services/statements.py` (or extend
  `services/invoices/pdf.py`'s Jinja env — it already has `money` / `kg` /
  `uom` filters + Indian grouping).
  - **Period resolver.** `period ∈ this_month | last_month | this_fy | last_90
    | custom`. `this_fy` = **1 Apr of the current Indian FY → today**
    (hard-wired April, no tenant setting). `custom` needs `dt_from` + `dt_to`.
  - **Opening-balance row.** First row is synthetic *"Opening balance as on
    <start>"* = the party's running balance carried forward from everything
    **before** the window (`party.opening_balance` + Σ earlier invoices − Σ
    earlier payments). Then the window's `party_ledger()` rows, then closing.
  - **Reversed payments** that fall in the window are **kept**, rendered
    struck-through with the reversal reason — never dropped (the balance column
    shows nil net effect, so the arithmetic ties out).
  - Closing line reads **"Total due ₹X"**.
  - Template `templates/statement_v1.html` — mirror the invoice template's
    header block + table rhythm. **UoM: `pcs` via the `uom` filter, never
    `nos`** (guardrail).
  - Regenerate on demand — no persisted `pdf_path`. Download name via the
    existing `download_name()` helper: `"<Party slug> statement
    <YYYY-MM-DD>.pdf"` where the date is **today** (generation date), matching
    invoice PDFs — *not* a period token.
- `GET /api/parties/{id}/statement.pdf?period=&from=&to=` → streams the file
  (`Content-Disposition` friendly name), mirrors the invoice PDF route.
- `POST /api/parties/{id}/statement/whatsapp` → body
  `{period, from?, to?, to_phone?}` → render + `_send_template_message()` with
  a **new template** `account_statement` (`party_name`, `total_due`, document
  header = the PDF). Add `"account_statement": ("party_name", "total_due")` to
  `TEMPLATE_BODY_PARAMS`. **422 if the window is empty.** Writes a
  `whatsapp_message` row — **no migration**: the table already has a nullable
  `party_id` FK (`models/whatsapp.py:51`), so the row is `party_id` set /
  `invoice_id` null / `template_name="account_statement"`.

**Frontend**
- `PartyAccountTab.tsx` header: a **period dropdown** (This month default) +
  **Download** + **Send on WhatsApp**. "Custom range…" reveals two native
  `<input type=date>` fields inline — same layout on phone + tablet. The WA
  button reuses the invoice-send recipient dialog (`WhatsappSendDialog` /
  `phone-input-and-save-slice`).
- **Empty window** → Send is disabled ("Nothing happened this period");
  Download stays enabled.
- `CollectionsPage.tsx` (F3a): a per-row **"Send statement"** link on
  **overdue** rows opens the same send dialog, party pre-filled, period =
  This month. Bulk multi-select send is **F3c**.

**Templates to submit to Meta on day 1 (1–2 business days review, longer if
rejected):** `account_statement` (F3b) and `payment_reminder` (already drafted
in `whatsapp.py:49`, needed by F3c). Submit both **before** or **in parallel
with** building F3b — the code can't send until `account_statement` is
approved, so start the approval clock first. Done in Meta Business Manager →
WhatsApp Manager → Message Templates by whoever manages the FleekWA app.

**Migration:** **none.**

**Effort:** ~2 days (statement template + period resolver + the two routes +
the header UI).

### A.3 Slice F3c — Reminder policy + approval queue + scheduler

**This is the only part of F3 that adds real infrastructure.** Everything
above is request/response; this needs a thing that wakes up daily.

**The scheduler question (decide before building):**
- **Option 1 — APScheduler in-process** (`BackgroundScheduler`, started in the
  FastAPI lifespan). Simplest, no new infra. Risk: multi-replica → double
  fire; the prod compose is single-replica for `metalerp-api` today (confirm
  in `fleek-infra`), so acceptable for M1 with a `SELECT ... FOR UPDATE` guard
  on a `scheduler_lock` row.
- **Option 2 — the companion agent (F10) as the cron.** It already checks in
  on a schedule; give it a "tick the reminder cycle" job. Ties F3c to F10
  being healthy at the shop — worse for a cloud-only tenant.
- **Option 3 — an external cron hitting `POST /api/reminders/run`.** infra
  owns a cron entry that curls the endpoint with an ops token. Clean
  separation, needs an infra change (memory: infra changes push `fleek-infra`
  first).
- **Recommendation: Option 1** (APScheduler + a DB advisory lock) for M1,
  with the actual work behind `POST /api/reminders/run` so it's also
  manually triggerable and testable. Revisit if `metalerp-api` ever scales out.

**Data model** (`0024` — verify head):
```
reminder_policy   id, tenant_id UNIQUE, enabled BOOL, offsets JSON
                  (e.g. [0, 7, 15, 30] — days past due),
                  quiet_start TIME, quiet_end TIME, max_per_week INT,
                  channel TEXT DEFAULT 'whatsapp', min_balance NUMERIC(14,2)
                  (don't chase < ₹X), created_at, updated_at
reminder_run      id, tenant_id, party_id, as_of DATE, bucket TEXT,
                  balance NUMERIC(14,2), oldest_invoice_id,
                  status ('queued'|'approved'|'sent'|'skipped'|'failed'),
                  wa_message_id, skip_reason, approved_by, sent_at,
                  created_at
                  UNIQUE(tenant_id, party_id, as_of)  -- one nudge per party per day, idempotent
party (add)       reminder_snooze_until DATE NULL   -- "leave them alone till X"
```

**Backend**
- Daily job (`services/reminders.py::build_reminder_runs(session, tenant_id, as_of)`):
  for each tenant with `reminder_policy.enabled`, walk `ageing_summary` rows;
  for each party whose oldest bucket age matches one of `offsets` (± the daily
  granularity), `balance >= min_balance`, not snoozed, under `max_per_week`
  → upsert a `reminder_run` in `queued`. Idempotent via the UNIQUE key.
- `send_approved_runs(session, tenant_id)`: for `status='approved'` rows,
  respect quiet hours, call `_send_template_message()` with `payment_reminder`
  (party_name, oldest invoice number, **party balance** not one invoice's total
  — the template's `{{3}}` becomes the running balance; adjust the approved
  Meta copy accordingly, i.e. submit the template worded for a balance).
  Flip to `sent`/`failed`, store `wa_message_id`.
- `POST /api/reminders/run` — ops/testing: build + (optionally) auto-send if
  the policy is set to no-approval. Behind `WriteUser` + a platform-admin or
  tenant-owner check.
- `GET /api/reminders/policy` · `PUT /api/reminders/policy`.
- `GET /api/reminders/queue?status=queued` — the pending batch.
- `POST /api/reminders/queue/approve` — body `{run_ids: [...] | "all"}`.
- `POST /api/reminders/queue/{id}/skip` — body `{reason}`.
- `POST /api/parties/{id}/snooze` — body `{until}` → sets
  `reminder_snooze_until`.

**Frontend** — new "Reminders" subtab on `CollectionsPage` (the page becomes
2-tab: Ageing / Reminders, following the Details/Account tab precedent from
`PartyAccountTab`):
- Policy editor card: enable toggle, offset chips, quiet hours, max/week,
  min balance, "auto-send or hold for my approval" switch.
- Approval queue: list of `queued` runs (party, balance, oldest bill, which
  offset triggered it), checkboxes, "Approve selected" / "Approve all" /
  per-row "Skip" + "Snooze 2 weeks". After approval the scheduler (or an
  immediate `run`) sends them.
- Sent log: recent `sent`/`failed` runs with the WA status
  (delivered/read/failed — reuse the webhook-updated `WhatsappMessage.status`).

**Open decisions**
- Per-party vs per-invoice reminder → **per-party balance, referencing the
  oldest open bill** (master doc's recommendation; also what the data model
  above assumes).
- Pay link in the message → **none for M1** ("reply PAID"); revisit UPI deep
  link later.
- Approval default → **hold for approval** on first enable (safer); tenant can
  switch to auto.

**Effort:** ~3–4 days (scheduler wiring + lock + the two job functions + the
subtab UI + template-copy iteration with Meta).

### A.4 Slice F3d — Promise-to-pay (smallest, optional)

- `promise_to_pay` table (`id, party_id, amount, promised_on, note,
  kept BOOL NULL, created_by, created_at`), migration `0025`.
- `POST /api/collections/party/{id}/promise`, `GET` on the party.
- UI: "Record a promise" on the party Account tab + a "Broken promises"
  filter chip on Collections (promised_on < today AND not kept AND still
  has balance).
- **Effort:** ~0.5 day. Do it only if an owner actually asks — it's a CRM
  nicety, not load-bearing.

### F3 build order & dependencies

```
[submit payment_reminder + account_statement to Meta]  ← day 0, blocks F3b/F3c sends
        │
F3a Ageing dashboard ──> F3b Statement→WhatsApp ──> F3c Reminder policy + queue + scheduler
   (tenant.credit_days)      (statement template)       (needs F3a's ageing_summary + approved template)
        │
        └──> F3d Promise-to-pay (independent, anytime after F3a)
```

F3 has **no F1 dependency** — it's a read-only consumer of invoices + payments.
If payments start coming from Tally (via F1 pull) later, ageing stays correct
because it reads our `payment` rows regardless of origin.

---

## Part B — F4 Mobile Owner-Operated Billing

### B.0 What already exists

Essentially the entire user-facing feature:
- Mobile-first invoice editor (`InvoiceEditorPage.tsx`) — line rows with
  Hindi/bartan search (F12), discount ₹/% toggle, weighment segments (F8),
  quick-create party, opening-balance block, 3-way finalize-time payment.
- `party_id` is **nullable on drafts** (`0013`), finalize still requires a
  party — so numbered invoices always have one.
- Payments from the phone (`PaymentDialog`, 3 launch points) — already built.
- Collections page — already the "who do I chase" screen.

### B.1 What F4 actually is

Per the master doc, F4 == **the Tally hand-off**:
1. Finalized invoice → enqueue a `tally_sync_job` (sales voucher).
2. Payment created → enqueue a `tally_sync_job` (receipt voucher).
3. Quick-created party/item → push as a Tally master *first* so the voucher
   references a real ledger.
4. A "Tally: synced ✓ / pending ⟳ / error !" chip on the invoice list + detail.

**Every one of those needs F1 (Tally Connector) to exist.** `tally_sync_job`
and `tally_link` are F1's tables. There is nothing to enqueue *to* until
F1a (masters-in) + F1b (sales-voucher-out) land.

### B.2 Recommendation

**Do not scope F4 as a standalone slice.** It is:
- `finalize_invoice()` (`services/invoices/finalize.py:124`) — after the
  existing best-effort PDF render, add a best-effort
  `enqueue_tally_push(session, invoice)` when the tenant has a linked
  `tally_company`. ~10 lines.
- `create_payment()` (`routers/payments.py:109`) — same, one call.
- Invoice list + detail responses — add `tally_sync_status` (derive from the
  latest `tally_sync_job` for that entity). ~1 aggregate join, same pattern
  as `payment_status`.
- `InvoiceListPage` / invoice detail — a small status chip. ~half a day of UI.

All of that belongs **inside the F1b slice** (master doc's S4 already pairs
them: "F1b + F4 — sales-voucher-out (file) + finalize enqueue + status chips").
Splitting it out just creates a slice that can't be tested end-to-end.

### B.3 The one thing to do now for F4

**Audit the pilot shops' Tally** (master doc §5.1, the stated blocker):
Gold vs Silver, single vs multi-company, is "act as server" on, LAN layout.
This decides F1's transport (live HTTP via the agent tunnel vs. nightly XML
file drop) and therefore whether F4's enqueue is near-real-time or batch.
This dev box: TallyPrime, `Client Server = None`, port 9000, company `100000`
— i.e. *not* currently acting as a server, so a fresh install would need
that toggled or we go file-transport.

### B.4 Pre-F1 groundwork that de-risks F4 (optional, small)

If you want to make forward progress before F1:
- Add the `tally_sync_status` field to the invoice list/detail response as a
  **stub that's always `null`** — gets the FE plumbing + chip component in
  place so F1b is pure backend.
- Nail down the **invoice-number-into-Tally** decision now (master doc's open
  question): carry our `invoice.number` into `VOUCHERNUMBER` + a UDF, Tally
  series set to manual. Document it in the F1 execution plan.
- Decide weighment → Tally mapping (F8 open item): weighment slips go into
  voucher **narration / UDFs**, not as extra stock lines (Tally inventory
  entries are qty×rate only).

**Effort for F4 proper (folded into F1b): ~1 day** on top of F1b's voucher
serializer.

---

## Combined recommendation

1. **Day 1, before/parallel to F3b code:** submit `account_statement` (params
   `party_name`, `total_due`, PDF document header) + `payment_reminder`
   (already drafted, `whatsapp.py:49`) to Meta. Review is 1–2 business days,
   longer if rejected — F3b's send route is dead until `account_statement` is
   approved, so the approval clock must start first. Also audit 3–5 pilot
   shops' Tally editions (unrelated, for F1).
2. **F3a** (ageing dashboard) — standalone, low risk, no Tally dependency.
   ~1.5 days. One migration: `tenant.default_credit_days` (verify `alembic
   heads` — F1a took 0022–0024, so likely `0025`).
3. **F3b** (statement → WhatsApp) — builds on F3a's Overdue chip + the existing
   WA path. **No migration.** ~2 days.
4. **F3c** (reminders) — the infrastructure slice; needs the scheduler
   decision (recommend APScheduler + DB lock). ~3–4 days.
5. **F4** — *not now.* Fold into the F1b slice per the master doc's S4.
   Do only the B.4 groundwork (stub field + chip component + number/weighment
   mapping decisions) if you want to parallelise.
6. **F3d** (promise-to-pay) — only on demand.

Total F3 (a+b+c): ~7–8 working days. F4: 0 until F1 exists.

---

## Cross-cutting notes / guardrails honoured

- **UoM: `pcs` display-only, never `nos` in data** — the statement PDF routes
  through the `uom` Jinja filter like the invoice template.
- **Migrations:** F1a took `0022`–`0024` — run `alembic heads` before writing
  any. F3a = **1 migration** (`tenant.default_credit_days`, likely `0025`).
  F3b = **none** (`whatsapp_message.party_id` already exists). F3c =
  `reminder_policy` / `reminder_run` / `party.reminder_snooze_until`. F3d =
  `promise_to_pay`.
- **Infra:** any scheduler-via-external-cron or new env var → push `fleek-infra`
  first (memory `infra-alignment`). APScheduler in-process needs no infra change.
- **No commits/pushes** — user does check-ins.
- **`WhatsappMessage` model:** confirm whether it's invoice-scoped before F3b
  — a party statement message may need a generalised subject FK.
- **Visual review before any UI coding** (memory `ui-plan-guardrails`) — the
  ageing strip, the Reminders subtab, and the statement layout each need a
  `docs/visual-plan/*.html` mock first.

# MASTER — Tally‑Complement SaaS: Feature Portfolio & Roadmap

Status: **living roadmap. S1 DEPLOYED 2026‑09‑09 (`caaf389` + `982a738`,
migrations `0022`–`0024`, party + bartan backfills run). S2 = F3a + F3b BUILT
2026‑09‑09, uncommitted (migration `0025`, 443 tests green). Parallel to S2:
**Tally Agent seamless onboarding BUILT + DEPLOYED + LIVE E2E CONFIRMED
2026‑09‑09** (migration `0026`, 452 tests green, commits `ca742b8`/`9e6c80d`
+ one more) — provisioning bakes the key into a cached installer zip in R2,
`install.ps1` self‑elevates + runs with zero arguments, checkin carries a
second signal (`tally_reachable`) distinguishing "agent installed" from
"Tally is actually reachable". **F1a is now validated live end‑to‑end**: a
real firm was provisioned, its installer downloaded and run on a real
Windows box already running TallyPrime as server, the agent checked in,
Tally reachability showed connected, and a real masters‑XML pull + import
succeeded. F2 delivery‑tracking deployed, e2e vs Meta still unconfirmed.**
See §1 for the per‑feature status table and §4/§6 for the running build log.

Owner: Fleek. Target market: Indian SMB traders (metal / bartan / utensils
first), currently on **Tally Prime**, accountant‑operated, desktop‑bound.

---

## 0. Thesis

Tally Prime is an entrenched, GST‑compliant **ledger** operated by a **trained
accountant on a desktop**. It is weak wherever:

1. the **business owner** (not the accountant) needs to act;
2. the workflow touches the **outside world** — customers, banks, suppliers, phones;
3. the work is **mobile**, **real‑time**, or **multi‑location**.

We do **not** replace Tally. We sit **on top of / beside** it:

- **Masters in** — pull ledgers (parties), stock items, groups, opening balances from Tally.
- **Vouchers out** — push sales invoices, receipts, and (later) purchases back into Tally as vouchers.
- We **own the workflows** Tally never did: WhatsApp send, collections follow‑up,
  AP bill capture, GSTR‑2B reconciliation, bank ingestion, weighment billing,
  old‑metal exchange.

The **Tally connector** (bidirectional sync) is the keystone build that turns
Metal ERP from a standalone ERP into a Tally companion.

### Go‑to‑market sequence

| Phase | Move | Why |
|---|---|---|
| **1. Wedge** | WhatsApp invoicing + collections reminders | Cheap, visible day‑one value, zero Tally disruption |
| **2. Land** | Companion agent beside their Tally (backup + health + sync channel) | Low‑risk, recurring, gets our agent installed |
| **3. Expand** | AP bill capture + GSTR‑2B reconciliation | High retention, "can't churn" modules |
| **4. Moat** | Verticalise: weighment, old‑metal exchange, Hindi/bartan search | Not competing with horizontal Tally add‑ons on price |

---

## 1. Feature portfolio at a glance

Legend: ✅ built & deployed · 🟢 built + committed, not deployed · 🟡 partly built · ⬜ not started

Last status pass: **2026‑09‑09** (after commits `caaf389`, `982a738` — now DEPLOYED).

| # | Feature | Tier | Status | Depends on |
|---|---|---|---|---|
| F1 | **Tally Connector** — masters in, vouchers out | Platform | ✅ **F1a (masters‑in) built + committed + DEPLOYED + LIVE E2E CONFIRMED 2026‑09‑09** — migrations `0022`/`0024`/`0026` applied on prod, agent `TallyMastersModule`, Ops `TallyPanel`. Seamless onboarding (provisioning bakes the key into a cached installer zip in R2, `install.ps1` self‑elevates + runs zero‑argument, checkin's second signal `tally_reachable` distinguishes "agent installed" from "Tally reachable", shop self‑serve download card). **A real firm's agent checked in, showed Tally connected, and a real masters‑XML pull + import succeeded** — closes the one open item F1a carried since it was built. F1b (voucher‑out) ⬜ next | Companion agent (F10) |
| F2 | **WhatsApp Invoicing** — send PDF, delivery receipts, per‑firm number | 1 | 🟢 send + phone‑normalise + opt‑in‑drop + "save number" + **delivery‑tracking fan‑in all DEPLOYED** 2026‑09‑09 (fleek‑infra + fleek‑backend + Metal ERP, in order). **e2e vs Meta (real send → delivered/read webhook advances the row) still unconfirmed.** | — |
| F3 | **Collections & Reminders** — ageing, auto WhatsApp nudges, statements | 1 | 🟡 **F3a (ageing dashboard) + F3b (statement→WhatsApp) BUILT 2026‑09‑09, uncommitted** — migration `0025` (`tenant.default_credit_days`), `ageing_summary()` + `GET /api/collections/ageing`, `services/statements.py` + `statement_v1.html` + `POST /api/parties/{id}/statement/whatsapp`, `account_statement` Meta template (**submit day‑1**), `CollectionsPage` rewrite + `StatementSendDialog`. 443 tests green. **F3c (reminder scheduler) ⬜ — the one real infra piece.** F3d (promise‑to‑pay) ⬜. | F2 |
| F4 | **Mobile Owner‑Operated Billing** — layman invoice + Collections, syncs to Tally | 1 | 🟡 editor/payments/opening‑bal all exist; F4 == "enqueue Tally push + sync chip". **Recommend folding into F1b, not a standalone slice.** | F1b |
| F5 | **AP Bill Capture** — photo/PDF → parsed draft → approve → voucher | 2 | 🟡 Inward pipeline X0–X5 exists; Tally push + OCR polish ⬜ | F1 |
| F6 | **GSTR‑2B / ITC Reconciliation** — pull 2B, auto‑match, "hold payment" flags | 2 | ⬜ | F5 |
| F7 | **Bank Statement Ingestion** — PDF/Excel → auto ledger‑coding → reconcile | 2 | ⬜ | F1 |
| F8 | **Weighment / Segment Billing** — multi‑weighing invoices, weight+count PDF | Moat | ✅ built (some parts committed in `739ba36`) | — |
| F9 | **Old‑Metal / Exchange Handling** — scrap in against new goods | Moat | ⬜ | F8 |
| F10 | **Companion Agent** — cloud backup + health monitor + sync transport | Platform | 🟢 backup deployed earlier; **per‑firm agent provisioning + `TallyMastersModule` committed `982a738`, DEPLOYED 2026‑09‑09** (shop‑side install still a manual JSON hand‑edit) | — |
| F11 | **On‑Prem Local‑First Box** — Postgres in Docker at the shop, one‑script deploy | Platform | ⬜ (proposal written) | F10 |
| F12 | **Hindi / Bartan Type‑Ahead** — synonym‑aware item resolution, price bands | Moat | ✅ built + **prod `backfill_bartan.py --apply` RUN 2026‑09‑09** (final trimmed spelling‑variant list) — F12 complete | — |

**Adjacent, not in the F‑list:** *Party dedupe* — operator free‑types "still/steel/SS", "Pvt Ltd" variants → duplicate parties. Backend + UI **built + committed `982a738` + DEPLOYED 2026‑09‑09** (migration `0023` `party.legal_name_normalized` applied, `resolve_party` ladder, structured 409 + `SimilarParties` picker; **`tools/backfill_party_namekey.py --apply` run on prod** — fuzzy key quality now full). Item‑side parity deferred. Docs: `EXECUTION-PLAN-party-dedupe-backend.md`, `docs/visual-plan/party-dedupe-review.html`.

Cross‑reference per‑feature / plan docs:
`EXECUTION-PLAN-F1a-tally-masters-in.md`, `EXECUTION-PLAN-party-dedupe-backend.md`,
`EXECUTION-PLAN-whatsapp-delivery-tracking.md`,
`EXECUTION-PLAN-invoice-list-streamline-and-wa-tracking.md`,
`EXECUTION-PLAN-phone-input-and-save.md`, `SCOPE-F3-F4-collections-and-tally-handoff.md`,
`FEATURE-whatsapp.md`, `FEATURE-payments.md`, `FEATURE-party-opening-balance.md`,
and memory index `~/.claude/.../memory/MEMORY.md`.

---

## 2. Feature specs

Each spec: **Problem · Tally gap · What we build · Data model · APIs · UI · Sync to Tally · Open questions · Effort**.

---

### F1 — Tally Connector (masters in, vouchers out)

**Problem.** Everything else in this portfolio is only a "complement" if data
flows both ways with the customer's existing Tally company. Without this we are a
rip‑and‑replace ERP, which is a much harder sell.

**Tally gap.** Tally exposes an **XML request/response API** over HTTP (default
port 9000, "Tally.ERP 9 / Prime acting as server") and can also
**import/export XML** files. There is no REST, no JSON, no webhooks, no
outbound push. Any integration is: our code speaks Tally XML, either
- **online** — HTTP POST to the Tally instance on the shop LAN (needs the
  companion agent F10 as a reverse tunnel, since the shop PC has no public IP), or
- **offline** — we generate an XML file, agent drops it into a watched folder,
  Tally auto‑imports (or the accountant imports manually).

**What we build.**

1. **`tally-connector` module** (backend) — a serializer/deserializer for the
   Tally XML dialect we need:
   - **Import (Tally → us):** `Ledger` (parties), `StockItem`, `StockGroup`,
     `Group` (ledger groups), `Currency`, bill‑wise `OpeningBalance`.
   - **Export (us → Tally):** `Sales` voucher (from finalized invoice),
     `Receipt` voucher (from payment), later `Purchase` voucher (from F5).
2. **Field mapping layer** — our `party` ↔ Tally `Ledger` under
   `Sundry Debtors`; our `item` ↔ Tally `StockItem` with unit, HSN, GST rate;
   our `invoice_line` ↔ Tally inventory entries; our tax split ↔ Tally
   `CGST/SGST/IGST` ledgers (names are per‑company config).
3. **Sync engine** — pull on a schedule + on demand; push on finalize/payment;
   idempotency via a stored `tally_guid` / `VOUCHERKEY` per record; a
   **sync log** with per‑record status and the raw XML for debugging.
4. **Two transports:**
   - **Agent transport** (preferred) — F10 companion agent holds a persistent
     connection to our cloud; cloud sends it XML jobs; agent POSTs to
     `http://localhost:9000` and returns Tally's response.
   - **File transport** (fallback) — cloud emits XML, agent writes it to
     `Tally\ImportData\`, watches `Tally\ExportData\` for masters.

**Data model (new).**

```
tally_company        id, tenant_id, company_name, base_currency,
                     tax_ledger_map (JSON: {cgst_ledger, sgst_ledger, igst_ledger,
                     round_off_ledger, sales_ledger, cash_ledger}),
                     debtors_parent, creditors_parent,
                     transport ('agent' | 'file'), last_masters_sync_at

tally_link           id, entity_type ('party'|'item'|'invoice'|'payment'|'group'),
                     entity_id, tally_guid, tally_masterid, tally_voucherkey,
                     last_pushed_at, last_pulled_at, checksum

tally_sync_job       id, tenant_id, direction ('in'|'out'), kind, payload_ref,
                     status ('queued'|'sent'|'ok'|'error'), tally_response,
                     error, created_at, completed_at
```

Migration: next free number (**`0022`** at time of writing — verify against
`alembic heads` before building).

**APIs.**

```
POST /api/tally/companies                     register a Tally company + ledger map
GET  /api/tally/companies/{id}/sync-status
POST /api/tally/companies/{id}/pull-masters   kick a masters import
POST /api/tally/invoices/{id}/push            push one sales voucher
POST /api/tally/payments/{id}/push            push one receipt voucher
GET  /api/tally/sync-jobs?status=error        review failures
POST /api/tally/sync-jobs/{id}/retry
```

Agent‑facing (M2M, `ShopAuth` from F10):

```
GET  /api/tally-agent/jobs/next               long‑poll for the next XML job
POST /api/tally-agent/jobs/{id}/result        return Tally's XML response
POST /api/tally-agent/masters                 upload exported masters XML
```

**UI.** Operations console → per‑firm "Tally" tab: connection status, "Pull
masters now", ledger‑map editor (dropdowns populated from a first masters pull),
sync‑job table with retry, raw‑XML drawer for support.

**Sync to Tally.** This *is* the sync feature.

**Open questions.**
- Do target shops run Tally Prime **Gold** (multi‑user, can act as server) or
  **Silver** (single‑user)? Silver still does file import/export; only the live
  HTTP API needs Gold + "act as server" enabled.
- Ledger naming is per‑company and inconsistent (`CGST` vs `Output CGST` vs
  `CGST @ 9%`). First pull must **enumerate existing ledgers** and let the
  operator map, not assume.
- Voucher numbering: let Tally assign (`AUTO`) vs. carry our invoice number.
  Recommend carry ours into `VOUCHERNUMBER` + our number in a UDF, Tally series
  set to manual for that voucher type.
- Conflict policy when a master changes on both sides between syncs.

**Effort.** Large. Suggest slicing: **F1a** masters‑in (read‑only, file
transport) → **F1b** sales‑voucher‑out (file) → **F1c** agent transport +
receipts → **F1d** ledger‑map UI + sync dashboard.

---

### F2 — WhatsApp Invoicing

**Problem.** Shops send bills by taking a photo of a printout. No delivery
proof, no professional look, manual every time.

**Tally gap.** TallyPrime has **no built‑in WhatsApp**. Sharing a bill means
export‑PDF‑then‑send, or a paid third‑party TDL add‑on.

**What we build.** Already built (`FEATURE-whatsapp.md`, memory
`whatsapp-invoice-slice`):
- One Meta app + one WABA, **per‑firm phone number** pasted into the Ops console
  (no Embedded Signup).
- `invoice_ready` template (approved) — document header + PDF attach.
- "Send on WhatsApp" button on **finalized** invoices, party‑checkbox +
  other‑number dialog, operator phone override.
- `POST /api/invoices/{id}/whatsapp`; shared `download_name()` for the attachment.
- Migration `0014` applied on prod.

**Remaining work.**
- Run the **full app path end‑to‑end** on a real finalized invoice (never done live).
- Wire `WHATSAPP_APP_SECRET` for `metalerp-api` so **webhook delivery/read
  receipts** are verified and stored against the message.
- Commit 2 pending files (signature‑block firm‑name removal + comment fix).
- Visually review the recipient dialog.
- **Message log UI** — per‑invoice: sent / delivered / read / failed, with resend.

**Data model.** Exists (`whatsapp_number`, `whatsapp_message` per the slice).
Add `status`, `status_at`, `wa_message_id`, `error_code` if not already there.

**Sync to Tally.** None needed — this is a pure workflow layer on our invoice.

**Effort.** Small — finishing, not building.

---

### F3 — Collections & Reminders

**Problem.** Owners don't know who owes them what, or chase it by memory.
"Bills Receivable" in Tally is a static report the accountant runs; no
follow‑up, no reminders, no customer‑facing statement.

**Tally gap.** Tally has ageing analysis but **no outbound collections
workflow** — no scheduled reminders, no "send statement" button, no
collection‑agent view, no promise‑to‑pay tracking.

**What we build.**

1. **Ageing dashboard** — per party: current / 30 / 60 / 90+ buckets, total
   outstanding, oldest unpaid bill, last payment date. Folds in
   `party.opening_balance` (already built, `party-opening-balance-plan`).
2. **Reminder engine** —
   - Cadence rules per tenant (e.g. day 0 on due date, +7, +15, +30).
   - Channel: WhatsApp (via F2 infra) using a **new approved template**
     `payment_reminder` (party name, amount, oldest bill, optional pay link).
   - Quiet hours, opt‑out, max‑per‑week cap, manual "snooze this party 2 weeks".
   - Dry‑run / approval queue so the owner sees the batch before it goes.
3. **One‑tap statement** — "Send account statement" → PDF of all open items +
   running balance (reuse the party‑ledger PDF work) → WhatsApp.
4. **Promise‑to‑pay** — mark a party "promised ₹X by date"; dashboard shows
   broken promises.
5. **Collection‑agent view** (later) — filtered worklist, log call outcome.

**Data model (new).**

```
reminder_policy      id, tenant_id, enabled, schedule (JSON: offsets in days),
                     quiet_start, quiet_end, max_per_week, channel

reminder_run         id, tenant_id, party_id, invoice_id?, scheduled_for,
                     status ('queued'|'approved'|'sent'|'skipped'|'failed'),
                     wa_message_id, approved_by, sent_at

promise_to_pay       id, party_id, amount, promised_on, note, kept (bool|null),
                     created_by, created_at
```

Migration: next free after F1.

**APIs.**

```
GET  /api/collections/ageing                  buckets, filterable
GET  /api/collections/party/{id}              open items + history + promises
POST /api/collections/party/{id}/statement    generate + (optionally) WA it
POST /api/collections/party/{id}/promise
GET  /api/reminders/policy  · PUT /api/reminders/policy
GET  /api/reminders/queue?status=approved     pending batch
POST /api/reminders/queue/approve             approve N / all
POST /api/reminders/run                       force a cycle now (ops/testing)
```

Scheduler: a daily job builds `reminder_run` rows from `reminder_policy` +
open items; owner approves in‑app; a second job sends approved rows via F2.

**UI.** New "Collections" section (you already have a Collections page —
extend it): ageing table up top, party drilldown, "Reminders" subtab with
policy editor + approval queue, statement button on party Account tab.

**Sync to Tally.** Read‑only consumer of invoice + payment data. If payments
are entered in Tally and pulled via F1, ageing stays correct.

**Open questions.**
- Template approval lead time — submit `payment_reminder` early.
- Pay link: UPI deep link / `upi://pay` with the firm's VPA? Or just "reply
  PAID". Start with no link.
- Do we remind per‑invoice or per‑party‑balance? Recommend per‑party with the
  oldest bill referenced.

**Effort.** Medium. Slice: **F3a** ageing dashboard (read‑only) → **F3b**
statement‑to‑WhatsApp → **F3c** reminder policy + approval queue + scheduler →
**F3d** promise‑to‑pay.

---

### F4 — Mobile Owner‑Operated Billing

**Problem.** The owner can't make a bill without the accountant and the
desktop. Sales that happen after the accountant leaves get written on paper.

**Tally gap.** Tally Prime is desktop‑installed; its **mobile app is
essentially view‑only**; the UI needs training. Owners describe it as "too
long to learn for what I actually need".

**What we build.** Mostly built — mobile‑first invoice editor, party ledger,
quick‑create party, line‑row with Hindi/bartan search (F12), opening balance,
discount ₹/% toggle, weighment (F8). Gap is **the Tally hand‑off**:

- Bills the owner makes on the phone during the day **queue as `tally_sync_job`
  rows** and post to Tally as sales vouchers (via F1) — overnight batch or on
  finalize. The accountant opens Tally next morning and the day's sales are
  already there, correctly grouped.
- Receipts entered on the phone (F3 / payments slice) post as receipt vouchers.
- Any master the owner quick‑creates (new party, new item) pushes as a Tally
  master first, so the voucher references a real ledger.

**Data model.** Reuses invoice / payment / party models + F1's `tally_link`
and `tally_sync_job`.

**APIs.** On `POST /api/invoices/{id}/finalize` and payment create, enqueue a
push job when the tenant has a linked `tally_company`.

**UI.** A small "Tally: synced ✓ / pending ⟳ / error !" chip on the invoice
list and detail. Ops sees the failures.

**Sync to Tally.** Core of the feature — see F1.

**Open questions.**
- Batch timing: on finalize (near‑real‑time, needs agent transport) vs. nightly
  file drop. Start nightly file; upgrade to agent.
- What if the accountant also enters the same bill in Tally? De‑dupe on our
  invoice number in the UDF; sync log flags a probable duplicate.

**Effort.** Small **once F1 exists** — it's mostly enqueue calls + status chips.

---

### F5 — AP Bill Capture (purchase side)

**Problem.** Every vendor bill (paper or PDF) is keyed into Tally by hand —
vendor, GSTIN, invoice no, date, every line, GST split. Hours per week, error‑prone.

**Tally gap.** Tally **does not auto‑ingest vendor invoices**. No OCR, no
email‑in, no PDF parser. This is one of the most‑cited Tally pains and a
crowded but validated add‑on category.

**What we build.** We already have the **Inward Bill Import pipeline**
(`EXTENSION-inward-bill-import.md`, memory `inward-execution`): X0 upload → X5
approve, fuzzy‑only line matching, migration `0004`, tested against the Sugal
Foods real PDF. Reframe + extend it as AP capture:

1. **Ingest channels:** upload, **WhatsApp‑in** (vendor sends bill to the
   firm's number → lands as a draft), **email‑in** (`bills@<firm>.fleek…`),
   phone camera.
2. **Extraction:** current parser is rules/fuzzy. Add an **LLM extraction
   fallback** for unseen layouts → structured `{vendor, gstin, inv_no, date,
   lines[], taxes, total}` with confidence per field; low confidence → human
   review card (already have review cards).
3. **Match:** vendor → our party (fuzzy + GSTIN exact); each line → our item
   (existing fuzzy line matching); GST rate sanity vs. HSN.
4. **Approve → Purchase voucher** into Tally (via F1), + optionally create a
   `bill` / payable record on our side to drive F3‑style payables ageing and F6.

**Data model.** Extend inward models: add `source_channel`, `raw_file_ref`,
`extraction_confidence`, `vendor_gstin`, `tally_link` on approve.

**APIs.**

```
POST /api/ap/bills                     upload / ingest
GET  /api/ap/bills?status=review
GET  /api/ap/bills/{id}               parsed fields + confidence + source image
PATCH /api/ap/bills/{id}              human corrections
POST /api/ap/bills/{id}/approve       → creates payable + enqueues Tally purchase voucher
```

**UI.** "Purchases / Bills" section: inbox (review / approved / rejected),
split‑screen review card (source image left, editable fields right, confidence
highlights), vendor + item match pickers.

**Sync to Tally.** Purchase voucher out (F1c/d). Vendor + item masters pushed first.

**Open questions.**
- OCR/LLM cost per bill vs. pricing. Cache by layout signature.
- GSTIN validation + supplier legal‑name lookup (public GST API).
- ITC eligibility flags feed F6.

**Effort.** Medium–large. Slice: **F5a** re‑skin inward as AP + WhatsApp/email
ingest → **F5b** LLM extraction fallback + confidence UI → **F5c** payable
record + ageing → **F5d** Tally purchase‑voucher push.

---

### F6 — GSTR‑2B / ITC Reconciliation

**Problem.** Shops **overpay GST** because they can't match their purchase
register to GSTR‑2B, and they pay suppliers who haven't filed (losing ITC).

**Tally gap.** Tally added some 2A/2B reconciliation, but it is
**accountant‑driven inside the desktop**, needs the return files loaded
manually, and has no "hold this payment" workflow tying recon to AP.

**What we build.**

1. **2B import** — upload the GSTN JSON/Excel for a period (later: GSP API pull).
2. **Auto‑match** our purchase register (from F5 payables) to 2B rows on
   GSTIN + invoice no (fuzzy) + taxable value + tax amount, with tolerance.
3. **Buckets:** matched · matched‑with‑diff · in‑books‑not‑in‑2B (supplier
   hasn't filed) · in‑2B‑not‑in‑books (missing bill).
4. **Actions:** "hold payment" flag on the vendor/bill (visible in F3/F5),
   "chase supplier" WhatsApp, "book missing invoice" → F5 draft.
5. **ITC summary** — eligible ITC this period vs. claimed, so the owner stops
   overpaying.

**Data model (new).**

```
gst_return_import    id, tenant_id, gstin, period (YYYY-MM), type ('2B'),
                     source ('upload'|'gsp'), row_count, imported_at
gst_2b_row           id, import_id, supplier_gstin, supplier_name, inv_no,
                     inv_date, taxable, igst, cgst, sgst, cess, filed_status
gst_match            id, tenant_id, period, bill_id?, gst_2b_row_id?,
                     status ('matched'|'diff'|'books_only'|'2b_only'),
                     diff (JSON), resolved (bool), action
```

**APIs.**

```
POST /api/gst/2b/import                  upload period file
GET  /api/gst/2b/reconcile?period=       buckets + counts
POST /api/gst/2b/match/{id}/resolve      accept diff / hold payment / create bill
GET  /api/gst/itc-summary?period=
```

**UI.** "GST" section: period picker, four bucket tabs, row‑level match view
with our‑bill vs. 2B‑row side by side, one‑click actions, ITC summary card.

**Sync to Tally.** Optional: write matched status back as a Tally note/UDF;
mostly this is our layer. Consumes F5 data.

**Open questions.**
- GSP partner for direct GSTN pull (Cleartax/Masters India/etc.) vs.
  upload‑only for v1. **Upload‑only for v1.**
- Matching tolerance defaults; rounding.
- Multi‑GSTIN firms.

**Effort.** Medium. **Hard dependency on F5** (need a structured purchase
register to match against). Slice: **F6a** import + auto‑match + buckets →
**F6b** actions (hold / chase / book) → **F6c** ITC summary → **F6d** GSP pull.

---

### F7 — Bank Statement Ingestion

**Problem.** Bank statements don't import cleanly into Tally; every line needs
a ledger code; reconciliation is manual.

**Tally gap.** Tally supports bank import only in **structured templates**;
most Indian bank PDFs / proprietary Excel exports need pre‑processing, and each
row still needs manual ledger allocation.

**What we build.**

1. **Parsers** for the common banks (SBI, HDFC, ICICI, Axis, Kotak, PNB, BoB)
   — PDF + Excel/CSV → normalized `{date, narration, ref, debit, credit, balance}`.
2. **Auto‑coding** — rules + learned narration→ledger map + fuzzy party match
   from narration (UPI names, NEFT remitter). Confidence‑gated.
3. **Reconcile** — match bank credits to our receipts (F3/payments) and bank
   debits to our payments/AP; unmatched → suggest a ledger or create a
   contra/expense entry.
4. **Push** matched entries to Tally as receipt/payment/contra vouchers (F1).

**Data model (new).**

```
bank_account       id, tenant_id, bank, acct_no_masked, ledger_name
bank_statement     id, bank_account_id, period_from, period_to, imported_at
bank_txn           id, statement_id, date, narration, ref, amount, dir,
                   balance, matched_entity_type, matched_entity_id,
                   suggested_ledger, status, tally_link
narration_rule     id, tenant_id, pattern, ledger_name, party_id?, hits
```

**APIs.**

```
POST /api/bank/accounts
POST /api/bank/statements/import        upload PDF/Excel
GET  /api/bank/txns?status=unmatched
POST /api/bank/txns/{id}/assign         set ledger/party, learn a rule
POST /api/bank/txns/bulk-push           enqueue Tally vouchers
```

**UI.** "Banking" section: import, txn table with status + suggested ledger,
bulk‑assign, "learn rule from this" checkbox, reconcile view (bank vs. books).

**Sync to Tally.** Contra / receipt / payment vouchers out (F1).

**Open questions.**
- Account Aggregator / bank‑feed API instead of PDF scraping (bigger, later).
- PDF password handling (statements are usually password‑locked).
- Parser maintenance burden — start with the 3 banks our pilot customers use.

**Effort.** Medium–large (parser‑heavy). Lower priority than F5/F6. Slice:
**F7a** 2–3 bank parsers + normalized table → **F7b** rules + auto‑coding →
**F7c** reconcile against our receipts/payments → **F7d** Tally push.

---

### F8 — Weighment / Segment Billing

**Problem.** Metal & scrap trades weigh goods multiple times per invoice;
the bill must show each weighing and a weight+count summary.

**Tally gap.** No native multi‑weighment; it's a paid industry add‑on category.

**What we build.** Built (`invoice-weighment-segments-slice`,
`weighment-mobile-totals-clarity`): migration `0011`
(`invoice_line.segment_no` + `invoice.weighment_slips` JSON), `domain/weighment.py`
+ `lib/weighment.ts` mirror, `measure_for()` on every read, finalize
auto‑closes open segments, PDF banded weighment rows + weight/count box,
running weight/count bar, Next‑segment dialog, mobile clarity pass.

**Remaining work.** Commit the uncommitted parts; visual review; run e2e;
ensure weighment data maps sanely into the **Tally sales voucher** (F1) — Tally
inventory entries are qty×rate; weighment slips likely go into voucher
narration or UDFs, not as extra stock lines.

**Effort.** Small — finishing + a mapping decision for F1.

---

### F9 — Old‑Metal / Exchange Handling

**Problem.** Customer brings old utensils/scrap and sets its value against new
goods on the same bill. Common in bartan retail; identical pattern to jewellery
"old gold exchange".

**Tally gap.** No native old‑stock‑inward‑against‑sale; **jewellery add‑ons
that do exactly this are top sellers on TallyShop** — validated demand.

**What we build.**

1. On an invoice, an **"Exchange / old metal received"** section: material,
   gross wt, less impurities/tare, net wt, rate/kg, **exchange value** (a
   negative line / deduction from the bill).
2. Optionally create a **scrap stock‑in** (into an "Old Metal" item/godown) so
   inventory and later scrap sales stay honest.
3. Net payable = new goods − exchange value; flows to F3 ageing and the PDF.

**Data model (new).**

```
invoice_exchange   id, invoice_id, material, gross_wt, tare_wt, net_wt,
                   rate_per_kg, value, creates_stock_in (bool), item_id?
```

Migration: after F1/F3.

**APIs.** Extend invoice create/update to accept `exchange[]`; recompute totals
(touch `tax.py` carefully — it's currently untouched by weighment; exchange is a
pre‑tax deduction, decide GST treatment).

**UI.** Collapsible "Old metal received" block in the invoice editor (mobile
card + desktop row); PDF shows "Less: Exchange (Old Metal, 4.2 kg @ ₹520)".

**Sync to Tally.** Sales voucher with a negative ledger line (exchange value to
an "Old Metal Purchase" ledger) + optional stock‑in journal. Needs F1.

**Open questions.**
- GST on exchange (is the old metal a purchase from an unregistered person?
  RCM? Usually treated as consideration‑in‑kind — get a CA ruling).
- Valuation disputes / re‑weighing.

**Effort.** Medium. Standalone once invoice + F1 exist.

---

### F10 — Companion Agent

**Problem.** Shop data lives on one PC with no backup; if the disk dies the
business loses its books. Also: we need a way to reach Tally behind the shop's
router.

**Tally gap.** Tally's own backup is manual/local. No health monitoring, no
off‑site copy by default.

**What we build.** Built & deployed (`tally-agent-backup-slice`,
`e97ed1d`/`920dfaf`/`0e4d8b6`, migration `0016` on prod): `tally-agent/` .NET
Worker Service — cloud backup sync + health monitor — `/api/tally-agent/*`,
`ShopAuth` M2M auth, modular for future Gateway modules.

**Remaining work.**
- **R2 bucket** provisioning + real shop **onboarding flow** (still open).
- Add the **F1 job transport** (long‑poll `jobs/next`, POST results) as a new
  agent module.
- Installer / auto‑update story for the .NET service on the shop's Windows box.
- Health dashboard in Ops (Tally reachable? last backup? disk space?).

**Effort.** Medium remaining (transport module + onboarding + installer).

---

### F11 — On‑Prem Local‑First Box

**Problem.** Shops distrust "my data in the cloud" and have flaky internet;
this keeps them on desktop Tally.

**Tally gap.** N/A — this is us matching Tally's "runs on my machine" comfort
while still being modern.

**What we build.** Proposal written (`FEATURE-onprem-and-rate-search.md`,
memory `onprem-and-rate-search-feature`): local‑first `docker compose` box at
the shop (**Postgres, not SQLite**; no native .exe), **GHCR images + one
PowerShell `deploy.ps1`** (install / upgrade / rollback / backup / restore /
status) on the shop's Windows/Tally box via Docker Desktop, `release.yml` on
`v*` tag, `/health`‑gated auto‑rollback. Sequencing A→B→C then D+E gate shop
rollout.

**Relationship to this portfolio.** The on‑prem box and the cloud SaaS run the
**same app image**; the companion agent (F10) syncs the local Postgres to
cloud. The Tally connector (F1) is *easier* on‑prem — the app and Tally are on
the same LAN, so the HTTP API works without a tunnel.

**Effort.** Large (infra). Independent track; not blocking F1–F9 cloud builds.

---

### F12 — Hindi / Bartan Type‑Ahead

**Problem.** Accountants type item names; misspellings and Hindi/English
variants (`balti`/`bucket`, `patila`/`patiala`) fragment the item master.

**Tally gap.** Tally item search is exact/prefix; no fuzzy, no synonyms.

**What we build.** Built (`bartan-synonyms-plan`, MERGED `2026-09-03`):
`BARTAN_SYNONYMS` (31 rows, Hindi‑canonical, spelling‑variants only),
`register` seeds synonyms, `backfill_bartan.py`, `apply_search` synonym‑aware
with `word_similarity` floor 0.60, `POST /api/items/resolve` returns full
`ItemListItem` + score, redesigned mobile‑first `LineRow`.

**Remaining work.**
- Prod `backfill_bartan.py --apply` with the final trimmed list (still PENDING;
  3 dup pairs already merged).
- Design note stands: hand‑kept synonym lists don't scale — keep small, lean on
  `word_similarity`.

**Effort.** Tiny — one prod command + monitoring.

---

## 3. Dependency graph

```
F10 Companion Agent ──┬──> F1 Tally Connector ──┬──> F4 Mobile billing→Tally
   (backup, deployed)  │      (keystone)         ├──> F5 AP Capture ──> F6 GSTR-2B
                       │                         ├──> F7 Bank Ingestion
F11 On-prem box ───────┘                         └──> F9 Old-metal exchange

F2 WhatsApp (built) ──> F3 Collections & Reminders     (F3 also consumes F1 payment pulls)
F8 Weighment (built) ──> F9 Old-metal exchange
F12 Type-ahead (built) — standalone
```

**Critical path to "a real Tally complement":** F10 (finish transport) → F1a/F1b → F4.

---

## 4. Proposed build order

| Sprint | Deliverable | Rationale |
|---|---|---|
| **S1** | **F2 finish** (e2e run, webhook secret, message log) + **F12 prod backfill** | Close two nearly‑done things; F2 is the sales wedge |
| **S2** | **F3a + F3b** — ageing dashboard + statement‑to‑WhatsApp | Immediate owner value, builds on F2, no Tally dependency |
| **S3** | **F1a** — masters‑in (read‑only, file transport) + ledger‑map UI | Start the keystone with the lowest‑risk half |
| **S4** | **F1b + F4** — sales‑voucher‑out (file) + finalize enqueue + status chips | First real bidirectional loop; demoable "bill on phone, appears in Tally" |
| **S5** | **F3c** — reminder policy + approval queue + scheduler | Turns collections from dashboard into automation |
| **S6** | **F10 transport module + F1c** — agent long‑poll jobs + receipts voucher | Near‑real‑time sync; retire the nightly file drop |
| **S7** | **F5a + F5b** — AP capture re‑skin + WhatsApp/email ingest + LLM extraction | Open the high‑retention purchase side |
| **S8** | **F5c + F5d** — payables ageing + Tally purchase voucher | Complete AP loop |
| **S9** | **F6a + F6b** — 2B import, auto‑match, buckets, actions | The "can't churn" compliance module |
| **S10+** | **F8/F9 finish**, **F7**, **F11**, **F6c/d** | Moat + infra + polish |

---

## 5. Open cross‑cutting questions

1. **Tally edition & topology of pilot customers** — Gold vs Silver, single vs
   multi‑company, LAN details. Blocks F1 transport choice. **Action: audit the
   3–5 pilot shops now** (this machine has Tally Prime — inspect it).
2. **Pricing model** — per‑module vs. bundle; per‑seat vs. per‑firm; on‑prem
   licence vs. SaaS subscription.
3. **GSP partner** for F6/GST direct pull — defer, upload‑only v1.
4. **WhatsApp template pipeline** — submit `payment_reminder` (F3) and any AP
   ack templates now; approval takes days.
5. **Data residency / trust messaging** — how we position cloud vs. on‑prem to
   Tally‑loyal owners.
6. **Support model** — raw‑XML drawers and sync logs are for *our* support team;
   staff them.

---

## 6. Build log

### 2026‑09‑09 — Tally Agent seamless onboarding BUILT + DEPLOYED + LIVE E2E CONFIRMED

`docs/EXECUTION-PLAN-tally-agent-seamless-onboarding.md`. Visual review
`docs/visual-plan/tally-agent-onboarding-review.html` → goal: **provisioning
an agent IS the installer** — the operator never sees a key, never assembles
a zip by hand; the backend does it in‑process at provision/rotate time.

- Migration **`0026`** — 3 nullable columns on `backup_shop`:
  `installer_r2_key`, `last_tally_ok_at`, `last_tally_status`
  (`connected|refused|no_company|unknown`).
- `app/services/tally/installer.py::build_and_cache_installer()` — calls the
  previously‑unwired `tally_agent_installer.build_installer_zip()` right
  after a key is minted (provision/rotate — the only two moments the
  plaintext key exists), uploads to R2 (`installers/<shop_id>.zip`),
  discards the plaintext. `_generate_appsettings()` gained the
  `TallyMasters` block it was missing.
- `backup_storage.py` — added `put_object` / `delete_object` (server‑side
  upload, no presign — the API builds the zip itself).
- `app/routers/admin.py` — provision/rotate now build‑and‑cache inline; new
  `GET /firms/{id}/tally-shop/installer` streams the cached zip.
  `FirmTallyShopProvisionResult.api_key` kept but deprecated‑empty (schema
  compat only — UI no longer shows a key box).
- **New `app/routers/tally_self_serve.py`** (`/api/tally/*`, `WriteUser`,
  tenant‑scoped) — `GET /agent-status` + `GET /installer` so a firm can
  self‑serve the *same* cached zip from its own login, not just via the
  operator forwarding a file.
- **New `app/services/tally/agent_health.py`** — one shared definition of
  "agent online" (`AGENT_OFFLINE_AFTER` = 5 min) and `tally_reachable_now()`,
  used by the admin/self‑serve status endpoints and by a new gate in
  `routers/tally.py::pull_masters`/`retry_sync_job`: a *fresh* non‑connected
  `tally_reachable` signal now 409s with an actionable message instead of
  queuing a job doomed to sit on "waiting on Tally".
- **Checkin gained a second signal** — `ShopCheckinIn.tally_reachable` +
  `tally_reason`, independent of `module_status`/`error`. Distinguishes
  "agent installed and phoning home" from "TallyPrime is actually
  reachable" — today only the first existed.
- **Agent (.NET):** `TallyGatewayClient.ProbeAsync()` classifies
  connected/no_company/refused/unknown off the same minimal envelope
  `IsReachableAsync` already used; `TallyMastersModule` probes every round
  (not just when a job is pending) and stashes the result on
  `AgentContext`; `TallyAgentService` passes it into the checkin call.
  `dotnet build` clean, 0 warnings — no test project exists for
  `tally-agent/` (confirmed absent, same verification level F1a shipped at).
- **`install.ps1` rewrite:** all params now optional, self‑elevates
  (`Start-Process -Verb RunAs`, re‑quoting bound params so the elevated
  relaunch stays zero‑argument), skips the key‑overwrite when the bundled
  config already has a real (non‑placeholder) key, prints a post‑install
  checkin‑confirmation nudge, fixed the stale `tools.make_backup_shop`
  reference, kept the UTF‑8 BOM / ASCII‑only discipline from the earlier
  PS 5.1 codepage incident.
- **Web:** `TallyPanel.tsx` — provision/rotate no longer show a key box;
  new "Installer" sub‑block (Download + Rotate‑and‑rebuild) and a "Live
  health" 2‑cell grid (*Agent → Fleek* / *Agent → TallyPrime*); "Pull
  masters" disables with an inline reason when reachability is bad. **New
  `components/TallyConnectCard.tsx`** — the shop‑facing "Connect your
  Tally" card, mounted on `FirmPage.tsx` (firm's own settings page),
  self‑hides once `tally_status === "connected"`. tsc/eslint/vite build all
  clean.
- **Decided against a shareable "Copy link"** in the operator UI — the
  download route needs a bearer JWT (same‑origin auth), so a plain URL
  can't work for someone without a login without reintroducing the token
  machinery the design deliberately dropped. Operator downloads the file
  and forwards it by hand instead.

**Suite: 452 passed / 2 skipped** (up from 424 at F1a's own commit — +28
tests: new `test_tally_agent_installer.py`, new `test_tally_self_serve.py`,
additions to `test_tally_connector.py` / `test_tally_agent.py` for the
reachability gate + checkin signal). `alembic heads` → `0026`.

**Deployed same day, then live‑e2e'd — and four real bugs surfaced that no
unit test could catch** (each needs a real container filesystem, a real
Cloudflare account, or a real elevated Windows relaunch to reproduce). Full
debugging narrative in the execution‑plan doc's "Post‑build live‑e2e
debugging" section; summary:

1. `install.ps1` path lookup assumed a dev‑only sibling‑directory layout
   that doesn't exist in the prod container (Dockerfile builds from `api/`
   alone) → 503 even with a real build correctly mounted. Fixed: fall back
   to looking *inside* `tally_agent_build_dir` itself (the one path a
   single‑directory compose volume actually guarantees is visible).
2. The `metalerp-tally` R2 bucket had never been created — one‑time manual
   Cloudflare step, not code.
3. Checkin/build failure logging was too thin to debug with — added real
   status/body logging server‑ and agent‑side.
4. **The actual root cause of "install.ps1 does nothing":**
   `[string]$SourceDir = (Join-Path $PSScriptRoot "publish")` as a
   **param‑block default** — `$PSScriptRoot` isn't reliably populated that
   early for every invocation style, and the elevated self‑relaunch hit
   exactly that gap. `Join-Path` threw on a null path *before any script
   code ran*, so the window closed silently in under a second, every time,
   with zero trace — explaining every failed reinstall attempt that day.
   Fixed: resolve `$SourceDir` in the script body with fallbacks; also
   added a `Start-Transcript` log + an end‑of‑run `Read-Host` pause so this
   class of failure can never hide again.

**Confirmed live, same day:** a real firm was provisioned through the Ops
console → real installer downloaded → `install.ps1` ran successfully on a
real Windows box already running TallyPrime as server → agent checked in
with the correct key → Tally gateway probe succeeded → console showed
**Agent → Fleek: Online** + **Agent → TallyPrime: Connected** → **a real
"Pull masters" ran and imported the masters XML successfully.** This closes
F1a's own open item from when it was first built. All fixes committed
(`ca742b8`, `9e6c80d`, + the `install.ps1` root‑cause fix). Deployed.

### 2026‑09‑09 — S2 = F3a + F3b BUILT (uncommitted)

Visual review (`docs/visual-plan/f3a-ageing-dashboard-review.html`,
`f3b-statement-whatsapp-review.html`) → decisions locked in
`SCOPE-F3-F4…` top block → built both slices.

**F3a — Collections ageing dashboard.**
- Migration **`0025`** — `tenant.default_credit_days INT NOT NULL DEFAULT 0`
  (0 = "due on invoice date" = pre‑column behaviour; tenant‑wide, no
  per‑party override).
- `app/services/payments.py::ageing_summary()` — per‑party buckets
  `lt30 / d30 / d60 / d90p` ("<30 days / 30+ / 60+ / >3 months"), boundary
  `≤29 / 30–59 / 60–89 / ≥90`. Due date = `invoice.date + credit_days`.
  Bucketing done in Python (dialect‑safe). Opening debit → bucket by
  `opening_balance_as_of` (lt30 if null); opening credit / on‑account →
  net only, never a bucket. Returns `worst_bucket` + `is_overdue`
  (`!= lt30`). Sorted worst‑bucket‑then‑total. Net‑credit / settled parties
  omitted.
- `GET /api/collections/ageing?q=&as_on=` + `AgeingRow` schema.
  `GET /api/collections` untouched.
- Web: `CollectionsPage.tsx` rewritten — scope chips
  `Owes us / Overdue / Overpaid / Either` (**radio‑exclusive**; Overdue ⊂
  Owes us). Owes us / Overdue read the ageing endpoint; Overpaid / Either
  fall back to `/collections`. 4‑cell tap‑to‑filter bucket strip, net total
  caption, worst‑bucket pill per row, per‑row "Send statement" on overdue
  rows → opens F3b dialog. `AgeingRow` / `AgeingBucket` in `types.ts`.
- Tests: `tests/test_ageing.py` (10) — boundaries, credit‑days shift,
  opening balance ±as_of, paid/credit absent, last_payment, search, `as_on`
  back‑date, sort.

**F3b — Party statement → WhatsApp.**
- **No migration** — `whatsapp_message.party_id` already nullable
  (`models/whatsapp.py:51`). A statement row = `party_id` set /
  `invoice_id` null / `template_name="account_statement"`.
- `app/services/statements.py` — `resolve_period()` (this_month default /
  last_month / **this_fy = Apr–Mar hard‑wired** / last_90 / custom),
  `party_statement_data()` (synthetic "Opening balance as on <start>"
  carrying pre‑window forward, in‑window rows, **reversed payments kept
  struck‑through with reason, nil balance effect**, `total_due` closing,
  `is_empty` flag), `render_party_statement_pdf()` (reuses the invoice
  Jinja env), `statement_download_name()` = `<slug> statement
  <YYYY‑MM‑DD>.pdf` (**today's date**, via the same convention as invoice
  PDFs).
- `templates/statement_v1.html` — mirrors the invoice header + table
  rhythm; `pcs` via the `uom` filter; closing line "Total due ₹X".
- `services/whatsapp.py` — `account_statement` added to
  `TEMPLATE_BODY_PARAMS` = `(party_name, total_due)`; `send_party_statement()`
  (empty window → `WhatsappError`; party‑phone or explicit `to_phone`).
- `routers/parties.py` — `GET /{id}/statement.pdf?period=&from=&to=`
  (422 bad range, 503 no WeasyPrint) + `POST /{id}/statement/whatsapp`.
- Web: `components/StatementSendDialog.tsx` (period dropdown + inline custom
  date fields, Download + Send recipient picker mirroring the invoice‑send
  dialog, "save this number" prompt). Wired into `PartyAccountTab.tsx`
  ("Statement" button) and `CollectionsPage.tsx` (overdue‑row link).
- Tests: `tests/test_statements.py` (11) — period presets incl. FY,
  custom‑range validation, carried‑forward opening, reversed‑payment kept,
  empty‑window flag, PDF route 422, send path (party phone / explicit /
  empty‑window reject / no‑phone reject), `account_statement` params.

**Suite: 443 passed / 2 skipped** (was 424). Web tsc + eslint + vite build
clean. **Day‑1 for deploy: submit `account_statement` + `payment_reminder`
Meta templates** — F3b send is dead until `account_statement` is approved.
`0002 CREATE EXTENSION` still not SQLite‑clean (pre‑existing; CI runs
migrations on PG). Nothing committed.

### 2026‑09‑08 → 09‑09 (commits `caaf389`, `982a738`)

**`caaf389` — "phone number validation and whatsapp tracking"**
- **Phone paste‑normalisation** — `validate_phone` (server) ⇄ `normalizePhone`
  (web) collapse `+91 / 91 / 0`‑prefixed and space/zero‑width‑laden pastes to
  `+91` + 10 digits; blur‑normalise on all party phone fields.
  (`EXECUTION-PLAN-phone-input-and-save.md`)
- **`whatsapp_optin` gate removed** from `send_invoice` — a send needs only a
  phone now (DPDP consent out of scope for this base). Column kept, unread.
- **"Save Other number to <party>?"** prompt in `WhatsappSendDialog`.
- **MASTER doc + delivery‑tracking plan** committed.
- *Not in this commit, still uncommitted‑then‑folded‑into `982a738`:* the
  fan‑in code + `_fmt_wa_error`. Fan‑in is **built, NOT deployed** — needs a
  fleek‑infra push (`METALERP_WHATSAPP_APP_SECRET` + compose vars) and a
  fleek‑backend push (`_fanout_whatsapp_webhook`).
  (`EXECUTION-PLAN-whatsapp-delivery-tracking.md`)

**`982a738` — "Tally companion … + party name duplication"**
- **F1a — Tally Connector, masters‑in** (`EXECUTION-PLAN-F1a-tally-masters-in.md`):
  migrations `0022` (`tally_connector`) + `0024` (one agent per firm, partial
  unique index on `backup_shop.tenant_id`); `app/services/tally/` (jobs, pull,
  staging); `app/routers/tally.py`; agent `TallyMastersModule.cs`; Ops
  `TallyPanel.tsx` (provision agent / rotate key / register Tally company /
  pull masters into the existing import review screen). Transport = **Tally
  HTTP Gateway** (verified live on this box, port 9000), folder‑read fallback.
  **No voucher push (F1b).** Built + tested, **NOT deployed**; infra wiring +
  live e2e are the only gaps.
- **Party dedupe** (`EXECUTION-PLAN-party-dedupe-backend.md`): migration `0023`
  `party.legal_name_normalized`; `app/services/party_resolution.py`
  (`resolve_party` ladder — GSTIN → phone → exact key → pg_trgm fuzzy);
  structured 409 (`party_exists` / `party_maybe_exists`) on create/rename with
  `?force=true` bypass; `POST /api/parties/resolve`; `PARTY_SUFFIX_SYNONYMS`;
  `tools/backfill_party_namekey.py` (**run `--apply` post‑deploy**). Web:
  `ApiError.detail` + `parseError`, `<SimilarParties>` panel in NewPartyForm +
  QuickCreatePartyDialog, PartyPicker debounced + role filter dropped.
- **Invoice list streamline + WA tracking UI**
  (`EXECUTION-PLAN-invoice-list-streamline-and-wa-tracking.md`): row redesign
  (payment = dot+word, ⋯ menu, PDF stays visible, "FINAL" label dropped);
  `WhatsappBadge` list column + `WhatsappLog` card on invoice detail
  (`components/WhatsappStatus.tsx`); new `GET /api/invoices/{id}/whatsapp`;
  `InvoiceListItem.whatsapp_status` via window‑fn subquery.
- **F3/F4 scoping** written (`SCOPE-F3-F4-collections-and-tally-handoff.md`) —
  no code.
- **backup_storage.py** + tally‑agent backend client scaffolding.

424 backend tests collected; suites green at commit time.

### 2026‑09‑09 — DEPLOYED

`caaf389` + `982a738` deployed to prod. Deploy sequence followed:
1. **fleek‑infra** pushed (`METALERP_WHATSAPP_APP_SECRET` export + compose vars).
2. **fleek‑backend** pushed (`_fanout_whatsapp_webhook`).
3. **Metal ERP** deployed — `alembic upgrade head` applied `0022`/`0023`/`0024`.
4. Backfills run on prod: `python -m tools.backfill_party_namekey --apply` and
   `python -m app.services.catalogue.backfill_bartan --apply` (final trimmed list).

**Still open (deployed but not validated live):**
- **F1a live e2e** — provision an agent, point it at a real Tally, pull masters
  through it. Never done. F1b/F4 stay blocked on this.
- **F2 e2e vs Meta** — real WhatsApp send → delivered/read webhook lands and
  advances the `whatsapp_message` row. Fan‑in path deployed but unexercised.
- Browser pass on the new party‑dedupe UI + invoice‑list redesign against prod.

---

## 7. Next step

S1 shipped + deployed 2026‑09‑09. S2 (F3a+F3b) built same day. **F1a live e2e
— DONE 2026‑09‑09** (Tally Agent seamless onboarding slice, §6): a real firm
provisioned, installer downloaded and run, agent checked in, Tally
reachability confirmed connected, a real masters pull + import succeeded.
That was the gate on F1b/F4 — **now unblocked.**

One validation task remains before new feature work is fully de‑risked:

- **F2 e2e vs Meta** — send a real invoice on WhatsApp, confirm the
  delivered/read webhook fans in from fleek‑backend and advances the row.

**F1b (sales‑voucher‑out, file transport)** is now the next Tally‑side slice
— unblocked. S2 (F3a ageing dashboard, F3b statement‑to‑WhatsApp) is already
built, uncommitted, no Tally dependency. Pick either next; F1b keeps the
Tally‑complement critical path (F10 → F1a/F1b → F4) moving, F3c (reminder
policy + scheduler) extends the already‑built F3a/F3b.

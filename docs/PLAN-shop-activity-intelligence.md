# Shop activity intelligence, and the first-invoice welcome (PLAN ONLY, 2026-10-29)

Status: **nothing built.** Decisions in section 5; mockup reviewed before code. Metal ERP stays a replica of Fleek's patterns, so the
parity table (section 4) is reviewed first.

## 1. What exists today (read from the code)

**Metal ERP records almost nothing about what a person does after sign-in.**
- `audit_log` holds only state changes (invoice finalise, inward approve), not who looked at what. No `last_seen`, no page or feature use.
- `track()` in `web/src/lib/analytics.ts` is Umami for the ad campaign: client-side, anonymous, not tied to a shop. It cannot tell Fleek
  "shop X is stuck".
- What can already be *derived* with no new capture: counts of parties, items, invoices (draft/final), payments, WhatsApp config,
  Tally link, and `tenant.created_at`. This is what `services/onboarding.steps()` uses.

**Fleek's Client Management is a wealth-advisor page.** It is built on `org_usage_snapshots`, `engagement_events`
("client-portal feature codes") and `activation_checklist` (families: roster, CAS, invites, goals). None of it describes a shop. I found
no `product_mode` filter in `org_usage_service` / `activation_checklist` / `admin_engagement_analytics`, so Metal ERP orgs probably
appear there as empty advisor orgs. Not confirmed against live data.

**Fleek already has the right bones to copy:** a stage funnel with a "stuck after N days" rule (`activation_checklist`), per-org
snapshots, `platform_events`, a Metal ERP shop page with tabs (`MetalErpShop.vue`: Overview / WhatsApp / Settings), and a signed
Metal ERP -> Fleek courier (`fleek_signed.request`, already used for codes, sign-up and feature requests).

## 2. Gap analysis: Welcome + Setup as the "create your first invoice" page

| # | Gap | Evidence | Effect |
|---|-----|----------|--------|
| 1 | The welcome page shows once, in one tab | `WelcomePage` reads a `sessionStorage` flag; without it, redirect to `/invoices` | Refresh, a second device, a WhatsApp deep link, or an operator-created user never sees it |
| 2 | First invoice is step 5 of 6 | Order is firm details, bank/UPI, customer, item, **invoice**, payment | Firm address and bank are not needed to make a first invoice; they delay the moment that matters |
| 3 | The checklist forces an order the product does not need | Invoices can be saved without a customer (party-optional slice) yet "Add your first customer" comes first | Extra steps before value |
| 4 | "Done" means a *finalised* invoice | `steps()` counts `status == final` only | A draft earns nothing; and nothing marks the real activation moment, **the invoice reaching the customer** |
| 5 | No "try it" path | Item groups are preset but there are no sample items or demo invoice | A new shop faces a blank editor |
| 6 | Pull-back after skipping is thin | A `SetupCard` already sits on the invoice list (corrected 2026-10-29: I first said there was none), but its button went to the Setup page, not the next step, and the empty list only said "start one with + New invoice" | Fixed in build slice 1: the card's button goes straight to the next step; the empty list has a "Make my first invoice" button |
| 7 | Copy promises six steps, not a result | "Six short steps will get your first invoice out" | The goal ("first invoice in 2 minutes") is not the headline |
| 8 | Nothing is measured | Only `signup-complete` in Umami; no step clicks, no editor opens, no abandon, no time-to-first-invoice | Cannot answer: sign-up to first invoice rate, median time, where people drop |
| 9 | Nobody is told when a shop is stuck | No signal reaches Fleek | CS finds out by chance |
| 10 | Follow-up channel got narrower | Email is now optional, so WhatsApp is often the only channel | Re-engagement by WhatsApp is a paid Marketing message outside the 24h window ([[whatsapp-promotional-cost-rule]]) |

**Recommended fixes for the page itself (small, no new tables):** one primary button "Make your first invoice" (customer and item
created inline in the editor); firm/bank become "before you send it"; add a "share it" step (activation = delivered); a persistent
progress card and empty-state on the invoice list until activation; welcome shown by server fact (`first_login` not yet
acknowledged), not a session flag; sample items one tap away.

## 3. Intelligence to build (the "after login" part)

### 3a. Capture in Metal ERP (one table, fixed vocabulary)
`activity_event(id, tenant_id, user_id, key, at, props jsonb)` plus `app_user.last_seen_at` (written at most once a few minutes).

- **Server milestones**, emitted where the thing already happens (no new hooks in the UI): `signed_in`, `firm_details_saved`,
  `party_created`, `item_created`, `invoice_draft_created`, `invoice_finalised`, `invoice_pdf_out`, `payment_recorded`,
  `whatsapp_connected`, `tally_linked`, `import_run`, `feature_requested`, `setup_viewed`.
- **A few client beacons** for friction: `setup_step_clicked`, `invoice_editor_opened`, `invoice_editor_abandoned`, `error_shown`.
  `POST /api/activity`, key whitelist, rate-limited, no free text.
- **Never stored:** party names, item names, amounts, phone numbers, message text. Counts and keys only.

### 3b. Ship to Fleek as a daily roll-up, not a stream
A scheduled job sends, per shop, over the signed courier: counts per key per day, `first_at` per milestone, `last_seen_at`,
active days (7/30). Fleek keeps `metal_erp_shop_activity(org_id, day, counts)` and a `metal_erp_shop_milestones(org_id, key, first_at)`.
Idempotent per (org, day). Metal ERP down or Fleek down loses nothing: the next run catches up.

### 3c. Fleek admin: where it lives
**Recommendation: a new "Activity" tab on the Metal ERP shop page and an activity strip on the Metal ERP shops list**, not
Client Management (it is advisor-shaped; stretching it would mix two products). Reuse its *patterns*: funnel stages, "stuck after
N days", an escalation list. If you want it inside Client Management anyway, the cost is a product filter plus a second funnel
definition there; say so and I will plan that instead.

What the admin sees:
- **Funnel (all shops):** signed up, firm details, first customer, first item, first draft, first final invoice, first PDF out,
  first payment, back on day 7. Counts, drop-off %, median time between stages. Filter by sign-up week.
- **Per shop:** stage reached, last seen, days active (30d), invoices per week with trend, features used, and a **stuck flag**
  (no stage progress for N days, N set in one place like `STUCK_AFTER_DAYS`).
- **Call list:** "signed up 3 days ago, no item" / "first invoice made, never sent out" / "was active, silent 10 days".
- **Health score** (simple, explainable): recency + weekly invoices + breadth of features. No model.

### 3d. Acting on it (later, after the read side proves useful)
In-app next-best-action on Setup (from the same milestones); a CS task per stuck shop; optional WhatsApp nudge only inside the
free 24h window or an approved template the owner opts into.

## 4. Fleek parity table (to review before code)

| Need | Fleek already has | Reuse / new |
|------|-------------------|-------------|
| Event store | `platform_events`, `engagement_events` | New small tables in Metal ERP (own DB); Fleek gets roll-ups, new `metal_erp_shop_*` tables |
| Stage funnel + stuck rule | `activation_checklist` | Same shape, shop stages; separate module (no families) |
| Transport | `fleek_signed` / `/api/internal/metal-erp/*` | One new route `.../orgs/<id>/activity`; no new secret, no new env var |
| Admin page | `MetalErpShop.vue` tabs, `MetalErpShops.vue` | New tab + list strip; no new top-level page |
| Daily job | `scripts/run_daily_jobs.py` pattern | Metal ERP side job (same scheduler as reminder proposals) |

## 5. Decisions (owner, 2026-10-29)
1. **Where it shows:** not only deep in the shop page. Fleek **admin Home** (first page after login) gets a Metal ERP band, plus a
   top-level **Shop activity** nav item, plus an **Activity** tab on each shop. Mockup: `docs/visual-plan/ShopActivity.html`.
2. **Consent line:** yes, one sentence in the terms/privacy page.
3. **Stuck threshold:** yes: no first invoice in 3 days, or silent 7 days (one constant each).
4. **Order:** do **both**, page fixes and capture, in parallel (they share the milestone keys).
5. **Activated** = first invoice **PDF downloaded or shared** (`invoice_pdf_out`), not just finalised. WhatsApp send is a paid upgrade: on Free it is a visible
   teaser that explains and offers to ask Fleek (`invoice_whatsapp_teaser_tapped` is recorded as upgrade demand), it sends nothing.
   The welcome "Your account is ready" is shown **once**, remembered on the server; afterwards the invoice-list progress card does the pulling back.
   **Nudges are email for now** (Fleek sends; free). Email is optional at sign-up, so a shop that gave none stays on the call list only.
6. Setup becomes five steps: firm details, first invoice (customer and item created inside it), send it, record a payment, bank/UPI.

## 6. Order of work (two tracks)
**Track A, Metal ERP page (no schema):** server-remembered welcome, "Make my first invoice" primary action, quick-add customer and item in
the editor, sample items, invoice-list empty state with progress card, reordered Setup, "send it" step.
**Track B, intelligence:** migration 0067 (`activity_event`, `app_user.last_seen_at`) and the key list, daily roll-up to Fleek, Fleek tables,
admin Home band, Shop activity page, shop Activity tab, terms sentence. Track A emits the same keys, so build the key list first.
Nudges to shops (gap 10) wait until the WhatsApp cost rule is settled.

## 7. Risks
- Measuring changes behaviour of the free plan's perceived privacy: say it plainly in the terms.
- Beacons must never block or slow the app: fire-and-forget, dropped on failure.
- Roll-up volume is small (shops x ~25 keys x days); no streaming infrastructure is justified.
- Funnel numbers are only as good as the stage definitions: "activated" = first invoice **PDF out**, agreed before the dashboard is built.

## 8. Plans, pricing and upgrade (2026-10-29; mockup `visual-plan/PlansPricing.html`)
Found: no billing or pricing page exists in Metal ERP. A newcomer sees "Free to start" (landing), "Not on your plan" + "Ask Fleek to switch this on"
(Setup), and "Change the plan in Fleek" (user limit). Fleek owns plans and prices (`metal_erp_plan_service`, editable in Plan templates).

**Decided (owner):** 399 / 999 / 1,499; monthly only; no GST; **payment self-serve from day one** (owner approves the hosted UPI AutoPay page themselves).

| Plan | Price / month | Users | Unlocks |
|------|---------------|-------|---------|
| Free | 0 | 1 | invoices, customers, items, payments; **7 days of Starter built in** |
| Starter | 399 | 1 | + download and share invoice PDFs (no WhatsApp, so **no order tracking**) |
| Growth | 999 | 3 | + **WhatsApp from the shop's own number** (Fleek hand-holds setup), order tracking, payment reminders, customer catalog parsing, inward price-list import, Tally backup to the cloud |
| Complete | 1,499 | up to 5 | everything, incl. **full Tally integration** (the headline USP) |

Order tracking moved to Growth because it needs WhatsApp. Hand-holding notes: `docs/RUNBOOK-whatsapp-handhold.md`.

**Trials (owner, 2026-10-29):** **Free = Starter for 7 days** (a new account downloads and shares PDFs; after day 7 it stays on Free with data kept and PDF download needs Starter).
**Growth and Complete: 3 days free, one-tap cancellation with no questions**, payment details taken up front (recommended: UPI AutoPay mandate, charged only when
the 3 days end), data always kept. A Growth shop in trial or paid whose WhatsApp is not connected by day 2 is on the Fleek call list. Emails: Free day 5 ("PDF download
ends in 2 days"), trial day 2 ("charged tomorrow, cancel here"). **Open:** take the mandate up front (recommended); extend a Growth trial by up to 4 days when WhatsApp
setup is delayed by Meta or us; refund of the first month if cancelled just after charge.

**What this changes in the build (bigger than a page):**
1. Gating is binary today (`features.is_free`). It becomes **per-plan feature sets**: each plan template in Fleek carries the feature keys it unlocks; Metal ERP
   gets them in the licence payload and `features.snapshot` returns per-key answers. `FEATURES` gains `pdf_out` (7-day window on Free, tracked by account age), `orders`, `catalog_parse`, `inward_prices`, `tally_backup`,
   `tally_full`; user limits come from the plan (1 / 1 / 3 / 5); `tally` splits in two (backup agent vs voucher push/masters pull). Today's Standard/Starter/Plus placeholders are replaced; **existing paid
   firms must be mapped, not downgraded** (owner decision 0.7: existing firms untouched).
2. **Self-serve trial + autopay:** Fleek endpoint a shop calls to start a 3-day trial on a chosen plan and get the hosted authorisation link (reuse
   `payment-link` + `create_subscription(trial_days=3)`); Fleek moves the plan, Metal ERP re-reads the licence. Trial end without approval = back to Free, data kept.
3. **Plans read live** from Fleek (`GET /api/internal/metal-erp/plans`, cached like the licence): no price hard-coded in Metal ERP.
4. Plan and billing page, header chip, locked-feature sheets naming the plan + price, Setup cards, landing prices (see mockup). Demand taps and trial
   starts become activity keys; email nudge on trial day 5 and to warm leads.


## 9. Build log
**Slice 1 (2026-10-29, Metal ERP only, uncommitted):** migration 0067 (`activity_event`, `app_user.last_seen_at`, `welcome_seen_at`); `services/activity.py` (fixed key
list, sanitised props, `touch_seen`, `first_times`); events recorded at party/item/draft/finalise/PDF-download/payment/firm-details/WhatsApp-connect/sign-in/sign-up/
feature-request; `POST /api/onboarding/activity` (whitelisted browser beacons, 300/h) and `POST /api/onboarding/welcome-seen`; `/me.show_welcome` (once, server-remembered);
Setup is five steps (firm, invoice, PDF out, payment, bank); welcome page leads to "Make my first invoice"; Setup card goes to the next step; empty invoice list has the button.
Tests: `tests/test_activity.py` (8) plus updated onboarding tests.
**Next:** per-plan feature gating and 7-day Free window (needs Fleek plan templates carrying feature keys); roll-up to Fleek + Fleek tables and admin pages;
plans page, trial and autopay; editor quick-add customer/item and sample items; "Download PDF" primary with the WhatsApp teaser; terms sentence.

**Slice 2 (2026-10-29, Metal ERP + fleek-backend + fleek-frontend, uncommitted):**
- *Plans and gating.* Fleek plan templates carry `unlocks` (the feature keys a plan switches on); new ladder keys `metal_erp_basic` (Starter 399, 1 user),
  `metal_erp_growth` (999, 3), `metal_erp_complete` (1,499, 5); the three older placeholder plans stay `legacy` with everything unlocked, so no shop on them loses a
  thing. Entitlements v2 carries `features`; Metal ERP `services/features.py` gates each key from it. Free = nothing, plus `pdf_out` for 7 days
  (`LADDER_LIVE_AT` = 2026-11-01 so a shop already on Free gets its 7 days from then). A self-signed-up shop whose paid plan lapsed is held to Free; Fleek-made shops keep the
  lenient rule. Routers gated: orders and share links (`orders`), supplier/customer catalogs (`catalog_parse`), inward bills (`inward_prices`), item-to-Tally
  (`tally_full`), Tally backup (`tally_backup`, old `tally` key still answers), invoice PDF (`pdf_out`). A request with no bearer header passes the gate to the route's own
  signed-URL auth (photos and PDFs opened by `<img>`/`<iframe>`).
- *Self-serve start.* `POST /api/plans/start` -> Fleek `start-plan` -> Free moves to the plan (`trial` 3 days for Growth/Complete; Starter 0 days = charged at once) and the existing
  `billing_service.start_checkout` opens the hosted autopay page with the **first charge at the trial's end**. `POST /api/plans/cancel` -> Fleek `cancel-plan`: a trial with no
  autopay goes back to Free now; otherwise the mandate is cancelled and the plan runs to the end of the trial/month. Cached licence is dropped after either.
- *Intelligence.* Daily roll-up Metal ERP -> Fleek (`services/activity_rollup.py`, hourly loop in `main.py`, runs once after 02:00 IST); Fleek `metal_erp_shop_activity`
  (migration `a19metalerpact01`), `services/metal_erp_activity_service.py` (funnel, status, call list), `/api/admin/metal-erp/activity` and
  `/api/admin/orgs/<id>/metal-erp/activity`; admin Home band, **Shop activity** page and a shop **Activity** tab. Established shops with no events are backfilled and
  count as activated, so they are not flagged stuck.
- *Nudges.* `services/metal_erp_nudges.py` + `job_metal_erp_nudges` in `run_daily_jobs.py`: email at Free day 5, trial ending tomorrow, stuck (days 3-6), quiet (7-9 days); once
  per shop, only to a shop that gave an email, **off until platform secret `METAL_ERP_NUDGES` = `on`** (until then the job only reports what it would send). The advisor trial-expiry
  email no longer fires for Metal ERP shops.
- *Web.* Plan and billing page (`/plans`) with the strong trial wording, header chip, upgrade sheet for any locked feature, plan prices on locked Setup cards and screens,
  Download PDF as the primary action with a "Send on WhatsApp / Upgrade" teaser, Free PDF countdown, plan section on My account, ladder on the landing page, usage sentence on sign-up.

**Not built, and why:** extending a Growth trial by up to 4 days (the autopay mandate's first charge is fixed at creation, so an extension needs the owner to approve a new
mandate; do it by hand until that flow exists); a "pick a time" booking for the WhatsApp session (the sheet says we will message the owner); sample items; the usage sentence on the
external privacy-policy page (fleekfintech.com, not in this repo); restarting a plan after a trial lapsed unpaid (Fleek only starts from Free; the owner must ask Fleek).

## 10. Go-live checklist (new)
1. Deploy **fleek-backend first** (plans, entitlements v2, start/cancel/activity routes), then run its migration `a19metalerpact01`; then Metal ERP (migrations 0066, 0067).
2. In Fleek **Plan templates**, check the three new plans (their prices come from the defaults until you save a row): Starter 399, Growth 999, Complete 1,499; edit `unlocks` there.
3. Fleek `scripts/seed_metal_erp_plans.py` is optional now (defaults answer without rows).
4. Leave platform secret `METAL_ERP_NUDGES` unset; read one day of the job's report (`would_send`), then set it to `on`.
5. Existing paid shops on the older plans keep everything; existing Free self-signup shops get 7 days of PDF download from 2026-11-01 (`LADDER_LIVE_AT`; change the constant to move it).
6. Metal ERP `ext_inward_import` / `ext_supplier_catalog` switches still apply on top of the plan.

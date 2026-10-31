# Sign-up follow-up console for Fleek admin (PLAN ONLY, 2026-10-29)

Status: **nothing built.** Parity table (section 3) and decisions (section 7) to be reviewed before code. Metal ERP stays a replica of Fleek's
patterns, so no new routes, secrets or env vars that Fleek lacks without saying so.

Builds on `docs/PLAN-shop-activity-intelligence.md` (the "after login" funnel and call list). This plan covers the part that plan does not:
**people who never get in**, and the **day-to-day act-on-it workflow** for a non-engineer.

## 1. The problem, from today's data (29 Oct 2026, read from prod)

Google Ads traffic: 13 people started sign-up, 3 got in, 8 more tried to sign in with no account.
- Eight of the nine unfinished sign-ups hit the old two-code flow; three never received a WhatsApp code (two "failed", one stuck at "sent").
- The failure is **invisible to the person**: the page does not say the code failed, and nothing follows up.
- The data exists but is **scattered and unreadable by CS**:
  - Metal ERP DB: `signup_request`, `otp_challenge` (attempts per channel), `auth_attempt` (`login_fail`, `signup_verify`, `code_send_*`).
  - Fleek DB: OTP sends in `whatsapp_inbound_messages` (`sent_by = system:metal_erp_code`, status `failed|sent|delivered|read`). The phone is
    **encrypted** there (`from_phone_enc`, `from_phone_index`), so only Fleek can match a send to a number; Metal ERP only knows the time.
  - Failed sign-ins are keyed `identifier|ip` in `auth_attempt` and carry no person.
- Today this took a manual SSH session and four joins. Nothing records what CS did after, so a call can be made twice or never.

## 2. Goal

A Fleek admin user (a CS / success person, not an engineer) logs in and sees **every lead and every shop as a state**, a **queue of who to
act on today**, the **script to use**, and a place to **record the outcome and the next action**. Almost nothing is typed by hand.

## 3. Fleek parity table (review first)

| Need | Fleek already has | Reuse / new |
|------|-------------------|-------------|
| Transport Metal ERP -> Fleek | `fleek_signed` courier, `/api/internal/metal-erp/*` (codes, sign-up, feature requests) | Reuse. One new route `.../leads/sync`. No new secret or env var. |
| Daily / periodic job | `scripts/run_daily_jobs.py` pattern; Metal ERP reminder-proposal scheduler | Reuse the Metal ERP scheduler; add a 5-minute lead push |
| Shop list and detail | `MetalErpShops.vue`, `MetalErpShop.vue` (tabs) | New tab "Follow-up"; list gets a state chip |
| Stuck rule / funnel | `activation_checklist` shape | Same idea; stages are the shop stages from the activity plan |
| Admin home band, "Shop activity" nav | Planned in the activity plan (decision 1) | Add one band: "Leads to call today" |
| Message log and status | `whatsapp_inbound_messages` | Read-only join by phone blind index inside Fleek |
| Admin roles | `routes/auth.py` shows roles `client`, `advisor`, `admin`; admin credentials come from config (`ADMIN_USER`/`ADMIN_PASS`) | Use the existing admin login for now (decision 2026-10-29). A scoped role is deferred. |
| Tasks / CRM | Fleek CRM (contacts, conversations, campaigns) is advisor-shaped | Do **not** stretch it. Keep a small shop-lead model. |

## 4. The model: one "lead" per mobile, one state machine

A **lead** is a mobile number seen on the Metal ERP site, from any source. It is created or updated from the sign-up and sign-in signals, and
it is closed when the person becomes an active shop.

States (derived by the system, never typed):

| State | Meaning | Source |
|---|---|---|
| `tried_login_no_account` | Failed sign-in, no matching account or sign-up | `auth_attempt` login_fail, no `app_user`, no `signup_request` |
| `signup_started` | Sign-up request pending, no code attempted | `signup_request` pending, attempts 0 |
| `code_not_delivered` | OTP send failed or only "sent" after N minutes | Fleek message status |
| `code_stuck` | Code delivered or read, wrong or no entries, expired | `otp_challenge.attempts`, `expires_at` |
| `account_empty` | Account made, no first invoice | `app_user` + counts |
| `first_invoice` / `activated` | Reached the milestones in the activity plan (activated = invoice PDF out) | activity roll-up |
| `silent` | Was active, no sign-in for 7 days | `last_seen_at` |
| `trial_wa_pending` | Growth/Complete trial, WhatsApp not connected by day 2 | plan + connection status (runbook section 6) |
| `won` / `lost` | Closed by CS or by activation | CS action |

Human fields, entered by CS: **outcome of last contact**, **notes**, **next action + due date**, **owner (assigned agent)**, `do_not_contact`.

Rules that keep this safe:
- **No codes, no passwords, no message text** are ever stored in the lead record. Counts and statuses only.
- The mobile is stored as given (it is the identifier CS must call). Access is limited to the agent role and audited.
- Only the **system** changes the state; CS changes outcome, notes, next action and assignment. This stops the board from drifting from reality.

## 5. What gets built

**A. Metal ERP: a lead feed (small).**
- A function that builds, per mobile, the facts above from the three tables and the activity roll-up.
- Pushed to Fleek every 5 minutes over the signed courier, idempotent per mobile (upsert by a stable key). Metal ERP down or Fleek down loses nothing: the next push catches up.
- Sign-up page fixes that remove the leak at the source (these shrink the call list, so do them first):
  1. Say plainly when a code could not be sent and offer a resend (the current error does not distinguish "not delivered").
  2. Sign-in with an unknown mobile or email answers "No account yet: create one" (today it only counts toward the lockout). Keep the generic answer
     for existing accounts so accounts cannot be probed: show it only on the sign-up path, or for a number that has no account **and** do it with the same timing.
  Both are changes to wording/flow, not to the one-code design.

**B. Fleek backend.**
- Tables: `metal_erp_leads` (mobile blind index + encrypted mobile, state, facts jsonb, first/last seen, source `ads|organic|operator`, owner_id, outcome,
  next_action, next_due, do_not_contact), `metal_erp_lead_events` (state changes and every CS action: who, when, what).
- `GET /api/admin/metal-erp/leads` (filters: state, owner, due today, source, date), `PATCH .../leads/<id>` (outcome, notes, next action, owner),
  `POST .../leads/<id>/message` (see D).
- A delivery enricher: for each lead, look up the latest OTP send by phone blind index and set `code_not_delivered` with the Meta status.
  This is the only place the encrypted phone is matched.
- Dedupe: a mobile that becomes a shop links to the Metal ERP org and moves from lead to shop automatically.

**C. Fleek admin UI.**
- New nav item **Follow-ups** (and the Home band "Leads to call today"). Not inside Client Management.
- **Queue view** (default): rows for today's work, ordered by state priority then age. Columns: name, firm as typed, mobile (tap to call / WhatsApp),
  state chip, what happened in one line ("code failed 08:18"), last contact, next action, owner. Filters and counts per state across the top.
- **Lead drawer**: timeline (sign-up attempts, code statuses, sign-ins, invoices), the **script for this state** (Hindi, from section 5E), and one
  form: outcome (dropdown), notes, next action, due date. Saving writes a lead event.
- **Shop tab "Follow-up"** reuses the same drawer for activated shops (stuck, silent, trial WhatsApp pending), so one place covers lead to customer.
- **Daily report**: counts by state and by outcome, calls made, accounts created, time to first invoice. Same screen the owner can open.

**D. Acting from the console (phased).**
- Phase 1 (no cost): click-to-call and a "copy script" button; CS calls from their own phone and records the outcome.
- Phase 2: one-tap **WhatsApp message** from Fleek's sender using an approved template, only where free (inside a 24h window the person opened) or
  where the paid Marketing price is accepted per [[whatsapp-promotional-cost-rule]]. `do_not_contact` and a daily cap are enforced server-side.
- Phase 3: automatic nudges by state (for example `code_not_delivered` -> resend prompt; `account_empty` day 1 -> email), each opt-in per rule.
- Email stays the free channel; many leads have none (email is optional), so the call list stays.

**E. Script library (Hindi), editable in the console.**
The scripts from the follow-up document (`Google Ads sign-ups: call list and Hindi scripts`) move into a `lead_scripts` table keyed by state, with
the plan card pulled **live from Fleek plans** (never typed), so the price quoted on a call cannot drift from the real plan. CS edits scripts without a deploy.

**F. Access.**
- **Decision 2026-10-29: Fleek admin only for now.** The Follow-ups pages sit behind the existing admin login and its session rules (72h absolute, 24h idle).
  User segregation (a scoped "success agent" role that sees leads and scripts but not invoices, parties, bank details, licence or secrets, and cannot create
  admins) is **deferred**. Keep every Follow-ups route behind one admin check so a role can be added later without touching the pages.
- Every view of a mobile and every action is written to `lead_events` and the existing audit log.
- Consent line in terms/privacy already agreed in the activity plan; add "we may contact you by phone or WhatsApp about your sign-up".

## 6. Order of work

| Phase | What | Why this order |
|---|---|---|
| 0 | Sign-up page fixes (A, fixes 1 and 2) | Shrinks the problem; no new infrastructure |
| 1 | Lead feed + Fleek tables + delivery enricher + Queue view + drawer + script library (admin login only) | The core: CS can work from one screen |
| 1a | Fleek webhook keeps Meta's failure reason in the inbox row (done 2026-10-29, `whatsapp_service.py`) | The delivery enricher needs the reason, not just "failed" |
| 2 | Home band, daily report, shop Follow-up tab (needs the activity roll-up from Track B of the activity plan) | Closes the loop for activated shops |
| 3 | One-tap WhatsApp (Phase 2 of D), then automatic nudges | Costs money and consent; do after the read side proves useful |

Tests: Metal ERP `DATABASE_URL=sqlite:///./_mytest.db python -m pytest -p no:randomly`; Fleek side needs the owner's go-ahead (its pytest shares
the live DB, `FLEEK_ENV=preview`). Never `ruff format` `api/`.

## 7. Decisions needed (owner)

1. ~~Success-agent role~~ **Decided 2026-10-29: Fleek admin login only for now; segregation later.** Because the admin login is shared, the action trail
   (`lead_events`) records the free-text "who" the person types on each action, not a verified user.
2. **Show "No account yet" on sign-in for an unknown number**: this reveals that a number has no account. Accept it (ads traffic, low risk), or keep the
   generic message and add a separate "New here? Create an account" link next to the sign-in box. Recommended: the link plus the generic message.
3. **Lead source**: are all leads Google Ads, or do we tag them (`utm`) at sign-up so the daily report separates ads from organic? Recommended: tag.
4. **Retention**: how long to keep abandoned leads' mobiles (suggest 90 days after the last contact, then delete or anonymise).
5. **Call volume**: who calls, in which hours, and how many per day? This sets the queue ordering and the daily cap.
6. **Pricing in scripts**: the plan prices are still a mockup. Scripts read them from Fleek, so only the open items (up-front payment, trial extension, refund) block the wording.

## 8. Risks

- **Contacting people who only tried to sign in** may feel intrusive; keep the first contact a help offer, honour `do_not_contact`, and keep the consent line.
- **Mobile numbers are personal data.** Limited role, audit trail, retention rule, and no codes or message text stored.
- **A wrong state hides a real problem.** States are computed from facts, not typed; the enricher's "not delivered" depends on Meta status arriving. If it never
  arrives, the state falls back to `code_stuck` after the code expires.
- **Scope creep into a CRM.** Keep it to leads and shops with one drawer. Campaigns and sequences stay in Fleek CRM.

## 9. Build log
**Slice 1 (2026-10-29, uncommitted, read-only queue, Fleek admin login only):**
- Metal ERP: `services/lead_feed.py` builds one lead per mobile (or email) from `signup_request`, `otp_challenge` and `auth_attempt` (`login_email` key, which has no IP);
  `main.py` `_lead_feed_loop` sends it every 10 minutes to `POST /api/internal/metal-erp/leads`, skipping an unchanged feed except about hourly. Tests: `tests/test_lead_feed.py` (6).
- Fleek backend: migration `a20metalerpleads01` (`{ops}.metal_erp_leads`), `services/metal_erp_leads_service.py` (feed validation, sealing, WhatsApp delivery join, pure state rules),
  `POST /api/internal/metal-erp/leads` in `routes/internal_metal_erp.py`, `GET /api/admin/metal-erp/leads` in `routes/admin_metal_erp_leads.py`, registered in `app.py`.
  Tests: `tests/test_metal_erp_leads_pure.py` (12, run with `--noconftest`; the Fleek suite itself was NOT run, it needs `FLEEK_ENV=preview` and the owner's go-ahead).
- Fleek frontend: admin page **Follow-ups** (`/admin/metal-erp/leads`, nav item, `MetalErpLeads.vue`, `metal-erp-leads.ts`, store `getMetalErpLeads`). Vitest 5 passed; `vue-tsc` shows no error in these files. **Not browser-verified.**
- Also: the Fleek inbox now keeps Meta's failure reason on a failed send (`services/whatsapp_service.py`).

**Slice 2 (2026-10-31): where each sign-up came from, shown on Follow-ups. SHIPPED AND REVERTED the same morning (commit ded615e, revert e29877e): production went down.
Cause: migration 0084 had `down_revision = "0083"`, but 0076-0083 (Tally work) were still uncommitted, so on deploy `alembic upgrade head` could not find 0083. Before re-applying: commit and deploy
0076-0083 first, or renumber 0084 to chain from the real head (0075 in prod), and run `alembic upgrade head` on a CLEAN CLONE first (CI does; the push-webhook deploy does not wait for it). The Fleek half
(backend + frontend) was never deployed and is unaffected; the Metal ERP half is in git history at ded615e.** Decision 3 of section 7 ("tag at sign-up") taken as: first-touch facts, source worked out in Fleek.
- Why: a sign-up (Salim ansari, 30 Oct) had no click id and the only way to see its source was matching Umami sessions by timestamp by hand.
- Metal ERP: `web/src/lib/analytics.ts` `rememberFirstTouch()` (7 days, utm tags, external referrer host, device, landing path + #anchor, first-seen time; same keys as Fleek's `useLeadTracking.ts`),
  sent with the sign-up as `attribution`. `services/attribution.py` cleans it and adds os / browser / in-app label from the User-Agent (raw UA never kept). Migration `0084`
  `signup_request.attribution` (JSONB). `lead_feed.py` sends the first attempt's attribution per lead (additive, feed version stays 1). Tests: `tests/test_attribution.py` (4), one in `test_account_onboarding.py`.
- Fleek backend: `metal_erp_leads_service.py` `clean_touch` (reuses `crm_lead_intake.clean_attribution`), `source_of` (click id or paid utm > other utm > referrer host > in-app > direct;
  no attribution and no click = "Not recorded", never guessed), `overview` adds `source`, `source_label`, `source_notes` per lead and `sources` counts. No migration (payload is JSON). 7 new pure tests (run with `--noconftest`; needs `requests` + `cryptography` in the env).
- Fleek frontend: Follow-ups page gets a Source column (badge + notes: device, in-app, campaign, landing, "signed up 2m after landing") and a source filter row. Vitest 6 pass; vue-tsc clean on these files. **Not browser-verified.**
- Limits: leads before this ships show "Not recorded" unless they carry a click id. The Umami session id is not stored (the client cannot read it); first-seen time stands in for it.
  An iPhone page with no Safari token is labelled "another app's browser" (a guess from the User-Agent, not proof it was Instagram).

**Deploy order:** Fleek first (migration a20 and the intake route), then Metal ERP. Metal ERP first is harmless: Fleek answers 404 and the feed logs a warning until Fleek is up.

**Known limits:** no call outcome, notes or assignment yet (slice 2); the page is a queue only. A person typing a made-up mobile into the sign-in box becomes a lead, and an
expired sign-up that nobody retried shows as "Code not entered", so read the reason line before calling. A lead's state comes from the newest sign-up attempt only.
Failed sign-ins carry no IP and no person beyond what was typed.

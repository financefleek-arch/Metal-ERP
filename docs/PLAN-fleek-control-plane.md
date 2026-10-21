# Fleek as the control plane for Metal ERP (onboarding, licence, WhatsApp, templates; login later)

Written 2026-10-20. PLAN ONLY, nothing built. Supersedes the "where does a shop connect" question in
`docs/PLAN-catalog-campaigns.md` section 8 (that plan now sits on top of this one).

Facts below come from reading fleek-backend memory and plans (B19, B23, B50, B53, B59, B52y) and the infra
compose file, not from running code. Items marked VERIFY need a code read before the phase starts.

## 1. Direction (owner, 2026-10-20)

Fleek admin should control every product. One step at a time:

1. Move **onboarding, licence, WhatsApp connection and templates** to Fleek now.
2. Move **login** to Fleek later, gradually.

Login was ruled out earlier because of cross-org complications (section 8). Deferring it is deliberate, and the
earlier decision (`platform-admin-slice`: no shared tables, separate password hashing, HTTP API only) still holds
for everything except login.

## 2. Principles

- **Control plane owns the org.** Org identity, licence and modules, billing, WhatsApp connections, template
  catalog. **Product owns its data.** Metal ERP keeps invoices, parties, items, users.
- **No shared tables, no cross-DB reads.** Fleek is `advisoros`, Metal ERP is `metalerp`, same Postgres instance.
  Integration is service-to-service HTTP only, signed, additive-only versioning.
- **Each phase ships alone**, with no behaviour change for existing shops until a per-shop switch is flipped.
- **Reuse, do not reinvent:** the module registry (`org_addons`, `module_entitlements`), billing
  (`compute_org_charge`, autopay), platform events and audit, template definitions and push, Embedded Signup and
  coexistence code, consent and delivery gate in the CRM.
- **Stable join keys now so login can move later:** `fleek_org_id` on every Metal ERP tenant, and room for a
  `fleek_user_id` on users.

## 3. Target model

| Concern | Owner | Metal ERP side |
|---|---|---|
| Org identity (name, status, contacts) | Fleek `organizations` | `tenant.fleek_org_id`, mirrored fields cached |
| Licence: module `metal_erp`, tier, limits, status | Fleek module registry | reads via entitlements API, caches |
| Billing, autopay, manual mode | Fleek | none |
| WhatsApp connection(s) | Fleek org-level connection | `tenant_whatsapp_config` becomes a cache of it |
| Template catalog and per-WABA approval | Fleek definitions + rollout | sends by name, reads approval status |
| Customer messaging engine (consent, gate, caps) | Fleek CRM core | supplies audience and personal links |
| Users and login | Metal ERP (for now) | unchanged until phase 6 |

## 4. Phases

### P0. Contract (design only, 1-2 days)
- Fix names: module key `metal_erp`, tier names, limit keys (counter seats, WhatsApp campaign monthly cap,
  contacts cap), and what "suspended" and "lapsed" mean to Metal ERP.
- Service-to-service auth: signed requests with a rotating shared secret (same pattern as the webhook fan-out
  and the agent release signing). One document listing every endpoint in both directions.
- Decide the VERIFY items: how `organizations` and `org_subscriptions` model a non-advisor org, and whether an org
  can exist with zero advisors (shops have none). If not, an org needs an "owner contact" instead of a login.

### P1. Org identity and provisioning
- fleek-backend: widen the `org_addons.addon_key` CHECK to include `metal_erp` (same migration pattern as b102),
  add the tier, show Metal ERP orgs in the Fleek admin org list with a product badge, and a "Create org" option
  that selects the product core(s).
- Metal ERP: `POST /internal/provision` (service auth) creates the tenant with `fleek_org_id`, seeds synonyms and
  taxonomy exactly as `/admin/firms` does today, and returns the tenant id. Migration adds `tenant.fleek_org_id`
  (nullable, unique).
- Existing shops: a dry-run script maps each tenant to a new or existing Fleek org, owner confirms per shop, then
  the link is written. Nothing else changes for the shop.
- Metal ERP Ops console stays for support, but "create firm" there is retired once provisioning works.

### P2. Entitlements
- fleek-backend: `GET /internal/orgs/{id}/entitlements` returns modules, tier, limits, status, billing state.
- Metal ERP: a small client with a cache and an offline grace period (Metal ERP is pitched on-prem). One gate
  function used everywhere, like `has_module()`. Start enforcing only tenant-active and the campaign cap; seats
  later.
- Billing: add `metal_erp` to `BILLABLE_ADDON_KEYS` so it is charged (the b102 follow-up found a silent undercharge
  when a key is missing from that list). Pricing is an open decision (section 7).

### P3. WhatsApp connection at org level
- fleek-backend: **reuse `advisor_whatsapp_connections` through the shop's owner member row** (see section 11), the same
  way CRM-only orgs already hold one org-wide connection (`org_whatsapp_service.connection_owner_for_sending`). No new
  table in this phase. One number per shop, which matches `UNIQUE(advisor_id)`; several numbers per org arrive later
  through the `purpose` seam B59 R3 already plans, for advisors and shops together.
- Connect: Fleek-assisted first. A Fleek admin runs Embedded Signup with the shop owner from the Fleek console
  (same popup, same code exchange, same `subscribe_app_to_waba`, coexistence skips PIN). Self-serve inside Metal
  ERP later by a signed hand-off. VERIFY: the Meta app's allowed JS-SDK domains and Embedded Signup config.
- Metal ERP: `tenant_whatsapp_config` is written by the control plane (push) or refreshed by pull, and is no longer
  pasted in by hand.
- Webhooks: replace the blind fan-out in `routes/intelligence.py` with routing by `phone_number_id` to the owning
  product, and attribute coexistence events to the org.
- Existing shops on the shared Fleek number stay as they are; moving one is an explicit per-shop step.

### P4. Templates from the Fleek catalog
- Add `metal_erp` to `whatsapp_template_definitions.audience`. Move `invoice_ready`, `payment_reminder`,
  `account_statement` and the three order templates into definitions so a newly connected shop WABA receives them
  through the existing push.
- Key approval status by `waba_id` (Meta approves per WABA), not by advisor. VERIFY the rollout table's key.
- Add **carousel** support once, in the shared builder and definitions. Dynamic URL suffix, per-send images and
  per-card text come in at send time, as in `docs/PLAN-catalog-campaigns.md` section 4.
- Metal ERP sends by template name and reads status from the control plane. No customer-typed template mapping
  (owner rule from the CRM work).

### P5. Campaigns through the CRM core
- A shop holds the CRM module as a second entitlement. Parties sync to CRM contacts with the consent source;
  campaigns run through CRM sends and gates; personal links come from Metal ERP; orders attribute back.
- Pilot: Fleek runs the first campaigns for one shop from the CRM, with no new shop UI. A thin campaign screen in
  Metal ERP over CRM APIs comes after the pilot.
- Needs a DPDP check (section 7).

### P6. Login (later, gradual)
Done only after P1-P4 are stable. Prepared now by: `fleek_org_id` on tenants, a nullable `fleek_user_id` on
`app_user`, and a password-hash version field so the hashing difference (PBKDF2 vs Argon2) can be migrated by
re-hash on login.
Steps when the time comes: Fleek issues a signed session token that Metal ERP accepts alongside its own login
(dual running, per shop switch); map users to Fleek identities by verified email or phone; then retire local
login per shop. Keep a local fallback for on-prem shops with no network route.
**The cross-org problem to solve first (why it was ruled out):** a Fleek user belongs to exactly one org
(`advisors.org_id`), while a person may own several firms or work for several. This needs a user-to-org
membership model with an org switcher before any shop moves.

## 5. What each side gets, per phase

| Phase | Fleek admin can | Shop sees |
|---|---|---|
| P1 | create and list shops as orgs | nothing new |
| P2 | suspend or limit a shop, bill it | nothing new unless lapsed or capped |
| P3 | connect a shop's number, see its status | number is theirs, no more shared sender |
| P4 | manage and push templates to shops | richer messages, new templates arrive automatically |
| P5 | run campaigns for a shop | catalog campaigns to consented customers |
| P6 | one login for everything | single sign-in |

## 6. Risks

- **Advisor-path coupling:** `org_whatsapp_service`, `crm_inbox_ingest` and others test `product_mode` or assume an
  advisor owns the connection. A new org-level table must not change behaviour for existing advisor orgs; keep it
  separate until proven, then migrate (B59's P1 lesson: a missed check silently disables a feature).
- **Offline shops:** entitlement lookups must not stop billing at a shop whose connection to Fleek is down. Cache
  plus grace, and a clear "last verified" in the Ops view.
- **Two sources of truth for WhatsApp config** during transition. The control plane wins; Metal ERP's copy is
  read-only once a shop is linked.
- **Meta:** per-WABA template approval, Tech Provider review limits, coexistence constraints (20 messages per second,
  14-day inactivity disconnect). Quality and tier are shared per business portfolio.
- **Customer data:** shops' customer phones reach Fleek's CRM in P5.

## 7. Decisions

**Settled (owner, 2026-10-20):**
1. **Org -> users hierarchy is the same as Fleek's** (org with advisors becomes org with users). Names can differ, the
   architecture stays. A Metal ERP shop is a normal Fleek org and its users are "members" of it. Whether member rows
   live in Fleek's `advisors` table or a new members table is a P0 design call: it decides how much advisor-keyed
   WhatsApp, billing and admin code is reused as is, and it is the table P6 login will use. Until P6 Metal ERP keeps
   its own `app_user`, so the Fleek org needs only an owner contact.
2. **The CRM core is sold separately, as an option** (a second module, as in B59). Not bundled into `metal_erp`.
3. **Fleek-assisted Embedded Signup** for the pilot, run against the checklist in section 9 so no detail already
   learned in Fleek is lost.

**Still open:**
4. Pricing and tiers for `metal_erp`, and for the CRM core when attached to a shop.
5. Where a shop owner runs campaigns after the pilot: a Metal ERP screen over CRM APIs, or the Fleek CRM app.
6. DPDP position for shops' customer data flowing into the Fleek CRM; whether the common Terms and DPA cover a shop as
   data owner and Fleek as processor.
7. Offline grace period length.
8. ~~Whether a Fleek org may have no member rows before P6.~~ Answered by the P0 read: it can exist in the database but
   the admin org list and the WhatsApp, template and flow ledgers all hang off an advisor row, so give each shop one
   owner member row (section 11).

## 8. Build order and first step

P0 -> P1 -> P2 -> P3 -> P4 -> P5, then P6. The first real code is P1 (module key, provisioning endpoint,
`fleek_org_id`). P3 and P4 are where the catalog campaign plan finally unblocks: one connected test WABA plus the
carousel definition is the spike in `docs/PLAN-catalog-campaigns.md`, and it can be done in parallel with P1-P2
on Fleek's own test number.

## 9. Fine details already learned in Fleek (checklist for every phase, do not rediscover)

Compiled 2026-10-20 from fleek-backend memory and plans (B23, B52y, B53, B59, CRM infra learnings). VERIFY = re-check
current code or Meta docs first. Each phase review ticks the relevant lines.

**Embedded Signup popup and code exchange**
- `FB.login()` runs synchronously inside the click handler: no `await` before it, and the callback must NOT be `async`
  (throws "asyncfunction, not function"; Chrome also blocks a popup opened after an await).
- Check the postMessage origin with `endsWith('facebook.com')`, not equality with `www.facebook.com`.
- Business Integration System User flow: `config_id` + `response_type: code` + `override_default_response_type`; send NO
  `redirect_uri` on the exchange (error 36008). Use a User access token config, not "System-user access token".
- Accept all five finish events (`FINISH`, `FINISH_ONLY_WABA`, `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`,
  `FINISH_OBO_MIGRATION`, `FINISH_GRANT_ONLY_API_ACCESS`); handling only `FINISH` silently ignores the rest.
- The Meta app's allowed domains must include any domain hosting the popup (VERIFY for Metal ERP's).

**After the grant, in this order**
- Do not store a per-shop token: Fleek's one System User token covers every shared WABA.
- Fetch the display phone number and business id server-side (the popup message carries neither).
- Cloud-API numbers: register with an operator-entered 6-digit PIN and store it; never auto-generate (two connections got
  stuck when nobody knew Meta's stored PIN). Without registration every send fails 133010.
- Coexistence (`featureType=whatsapp_business_app_onboarding`, `version v4`, `sessionInfoVersion 3`): skip PIN and
  registration; deregistering these numbers is forbidden; the profile push may be refused, so handle and report it.
- **Subscribe the app to the shop's WABA** (`POST .../subscribed_apps`). Dashboard config alone delivers nothing per WABA;
  status silently froze in production before this was added.
- Push templates to the new WABA and track per-WABA approval from the webhook.

**Coexistence behaviour**
- The number must be on the WhatsApp Business app (>= 2.24.17), active, and NOT on regular WhatsApp (error 2494064). A WABA
  already shared with Fleek from an earlier test caused "Couldn't share WhatsApp account with partners".
- Enable and capture `smb_message_echoes`, `history` (24 h, once per onboarding), `smb_app_state_sync`, `account_update`,
  plus `user_preferences`, `business_capability_update`, `account_alerts`.
- 20 messages per second per shared number; the app disconnects after about 14 days unused; groups, disappearing and
  view-once messages and broadcast lists are unsupported.
- Echoes must not open the 24 h window or count towards message counts. Phone-app disconnects (`PARTNER_REMOVED`,
  `account_offboarded`) are handled nowhere in Fleek yet, so build that in P3.

**Webhooks and sending**
- Verify `X-Hub-Signature-256` against the shared App Secret; in production fail closed if unset. Forward raw bytes.
- The 24 h window is per sender number; reply from the number the customer wrote to (131047 otherwise).
- Classify Meta errors (131049 over-messaged, 131047 window, 131042 billing or test number) at the right scope: contact,
  message, or whole account. A 200 from the send API is not delivery; failures arrive on the status webhook.
- No hardcoded Graph version (v18 had expired); use a setting.
- Messaging limit is per business portfolio (250, 2,000, 10,000, 100,000), read by poll and webhook; effective cap is the
  lower of our plan cap and Meta's. Test numbers cannot send marketing (131042).

**Templates**
- Meta may reclassify a template or reject it `INCORRECT_CATEGORY`; wording that reads like an OTP or an instruction to act
  is risky. Approval is per WABA, and Fleek's own WABA status has no webhook feed (attested by hand today).
- Fleek's WABA id is the `FLEEK_WABA_ID` secret; `WHATSAPP_SENDER_NUMBER` holds a phone_number_id, not a number. The WABA
  cannot be derived from it.
- No customer-typed template or button mapping, no per-customer template editor (owner rule).
- New scripts reach containers only by commit, push and rebuild, then `docker exec`.

**Consent and customer protection (CRM core)**
- Consent keyed org x contact x channel x purpose; every outbound passes the single delivery gate.
- STOP and phrase detection, Meta `user_preferences` stop and resume, spam-complaint and block handling, "asked to stop"
  state, wrong-number handling. A typed "yes" does not re-consent; only the person can.
- Frequency guard, attempt cap and "Undecided" state; Meta's own per-user marketing cap also applies.
- Strict sender policy: never fall back silently to Fleek's shared number; that number is a per-org switch
  (`use_fleek_channels`) and a fallback for tests only.
- Meta bills the org directly. Fleek shows counts by category and a link to Meta's insights, not a rupee estimate.

**How we work in fleek-backend (process)**
- Real DB tests only on an isolated throwaway schema, one pytest process at a time (slow is not hung); ask before any DB or
  VPS action; never write `.env`; do not commit; do not amend or force-push (the VPS deploy is fast-forward only and fails
  silently); plan files are mirrored (`Plan/` and `plans/`, byte-identical); check a module path is free before writing;
  mockup first for any UI; "shipped" is not "working", so verify live.
- Infra-side change first (`fleek-infra` push), then the app repo; a new env var touches three places.

## 10. Fleek-assisted pilot runbook (draft, firmed up in P3)

1. Pre-checks: the shop number is on the WhatsApp Business app and active for about a week, the app is current, the owner
   is a Facebook admin of their business, and the shop has a payment method ready for its WABA.
2. A Fleek admin starts the connect from the Fleek console with the shop owner present (popup, synchronous handler).
3. On finish: exchange the code, store the connection, fetch display number and business id, subscribe the app, push
   templates, record `connection_mode` and the finish event.
4. Confirm: webhook subscription present, `smb_*` fields on, a test `order_update` reaches the owner's phone, the delivery
   status arrives, the messaging tier is read.
5. Record in the control plane who connected, when, and the entitlement state `connected`.
6. Rollback: revoke the local row and fall back to the shop's previous sender. Nothing is deregistered on Meta for a
   coexistence number.

## 11. P0 findings (read of fleek-backend code, 2026-10-20, nothing changed)

**What the schema is**
- `organizations`: `id`, `name`, `registration_type` (`individual` | `firm`), `created_by`, plus later columns such as
  `product_mode` (`full` | `crm_only`). Very thin. Orgs have no owner contact of their own.
- `advisors` is the member table: unique `username` (the email), sealed email and mobile, `password_hash`, `role`
  (`org_admin` | `advisor`), `org_id`, `approval_status`, and advisor-specific fields (AMFI number, advisor type, public page).
- `org_subscriptions` (one plan per org, `billing_mode` autopay or manual) and `org_addons` have `org_id` as plain text with
  **no foreign key**, so a row set can exist for an org that has no advisor.
- Every WhatsApp ledger is advisor-keyed: `advisor_whatsapp_connections` (unique per advisor), `advisor_whatsapp_template_rollout`
  (unique per advisor and template, with `waba_id`, `meta_template_id`, status, rejection reason) and
  `advisor_whatsapp_flow_rollout` (same shape for Flows, B58). All three cascade-delete with the advisor.
- `POST /api/admin/orgs` (`create_org_and_advisor`) always creates the org and its first advisor together, as `org_admin`,
  starts a Planning trial by default, issues a "set your password" link and sends a welcome email. `GET /api/admin/orgs` is
  built by grouping `advisors`, so an org with no advisor row never appears in the Fleek admin list.
- The connect route is `require_auth("advisor")`: it acts only for the signed-in advisor, so a Fleek admin cannot run it on a
  shop's behalf today.

**Consequences**
1. Flows are part of what Fleek holds, so P4 covers templates **and** Flows (`advisor_whatsapp_flow_rollout`), not only templates.
2. The cleanest reuse of "org and users, same architecture" is: **each Metal ERP shop is an org with one owner member row**
   (an `advisors` row, `role=org_admin`), marked as a Metal ERP org. That one row carries the connection, the rollout ledgers,
   seats and billing, with no schema changes to those tables. Other shop users stay in Metal ERP until P6, when they become
   members of the same org.
3. That row must not give a shop owner access to the advisor app. Widen `product_mode` (or the derived `shell_for`) with a
   `metal_erp` shell whose route allow-list is billing, team, WhatsApp connection, profile, and `/api/crm/*` only if the CRM
   core is held (default-deny, like `crm_only`). No welcome email or set-password link in P1; those belong to P6.
4. Global `advisors.username` uniqueness means a person cannot be a member of two orgs. That is the cross-org problem. For now:
   refuse with a clear message if a shop owner's email already belongs to a Fleek advisor, and use an agreed contact email.
5. Provisioning is a new admin path, not a reuse of `create_org_and_advisor` as it stands: no trial, no password, no email,
   `registration_type=firm`, `metal_erp` module row, then call Metal ERP's provisioning endpoint and store `fleek_org_id`.
6. Connect needs an admin variant: `POST /api/admin/orgs/<id>/whatsapp/connect` acting on the org's owner row. The popup runs in
   the existing Fleek admin shell (same frontend, so the Meta app's allowed domains should already cover it; VERIFY).
   `whatsapp_connect_block_reason` caps numbers by CRM plan, so it needs a rule for `metal_erp` orgs (one number).
7. Billing: `compute_org_charge` adds `metal_erp` to `BILLABLE_ADDON_KEYS`; the module row's CHECK needs widening, as b102 did
   for `crm`.

**Choice made here (revisit before P1):** owner-row model, because every existing ledger and flow works unchanged and CRM-only
orgs already follow the same pattern. Cost: one advisor-shaped row per shop that carries advisor-only fields unused, and the
username-uniqueness limit above until P6.

**Still unread (VERIFY before P1):** how `_product_mode_gate` and `shell_for` are applied on the frontend shell for a third
mode; `org_subscriptions` behaviour for a shop with no plan; the `set-password` and welcome-email paths that must be bypassed;
the platform-events and audit entries a new org type should emit.

## 12. P1 read results (fleek-backend code, 2026-10-20, nothing changed)

Confirms the owner-row model (section 11) and adds what it must handle. Decided by the owner on 2026-10-20: owner-row model
accepted; a shop owner's email must not already belong to a Fleek advisor.

**Findings**
1. **Planning is implicit.** `module_entitlements.holds_planning(product_mode)` is true for every org except `crm_only`. A Metal ERP
   owner row would therefore be treated as a Planning org. Fix: add `metal_erp` to `product_mode` (widen the CHECK, `PRODUCT_MODES`),
   make `holds_planning` false for it, and extend `shell()` / `shell_for()` with a `metal_erp` shell. The B59 lint
   (`scripts/check_crm_entitlement_usage.py`) ceilings every `product_mode` use, so the new value goes in as a shell answer (Q2), not as
   an access check.
2. **The product gate is allow-list only for `crm_only`.** `app.py::_product_mode_gate` restricts a crm_only session to
   `CRM_ONLY_ALLOWED_PREFIXES` and leaves everything else open. A `metal_erp` mode needs its own default-deny allow-list (billing,
   team, WhatsApp connection, profile; `/api/crm/*` only when the CRM core is held). `_legal_gate` also looks at `crm_only` only.
3. **Login has several doors.** Password login (`routes/auth.py::login`, advisor lookup by username with `is_active` and
   `approval_status='approved'`), Google login (`login_google`, by `email_index`), forgot-password, set-password and invites. A shop
   owner who used "forgot password" with the contact email would otherwise get a full advisor session. Until P6: deny sign-in for any
   org whose `product_mode='metal_erp'` at every door, **and** keep the default-deny gate in 2 as the second layer.
4. **Access tier comes from `org_subscriptions`.** `get_org_access` returns the free tier when an org has no subscription row, and
   `sending_blocked_reason` (CRM sends) follows that tier. A shop with no Planning subscription would therefore be treated as lapsed.
   Fix: for `metal_erp` orgs, derive access from the `metal_erp` module row (status, period, billing mode), reusing the same
   autopay, grace and manual-mode rules where they apply.
5. **Org creation path** (`create_org_and_advisor`): trial, welcome email and set-password link are all unwanted for a shop. The new
   provisioning path calls the same helpers (`signup_service._create_org`, `_create_advisor_row`) with `registration_type='firm'`,
   no trial, no email, `product_mode='metal_erp'`, a random unknown password (as the existing code does when none is given), then the
   `metal_erp` module row. `platform_events.emit` takes an event type per call; a shop-created event is a new type (VERIFY the
   allowed list).

**Effect on test emails (owner flagged it):** any existing Fleek advisor or test account email cannot be reused as a shop owner's
contact. For tests use a plus-address (`name+shop1@domain`): it is a distinct username to Fleek and still lands in the same
mailbox. Real shops are unaffected unless the owner is already a Fleek advisor, which is the cross-org problem P6 solves.

**Size of the P1 backend change (estimate from the read):** one migration (product_mode value, `metal_erp` module key, new event
type), `module_entitlements` and `crm_plan_service` constants, the shell and gate allow-list, the auth-door checks, an admin
provisioning route, and tests for the access matrix {planning, crm_only, both, metal_erp} including a leak test that a
`metal_erp` session reaches no Planning route. It follows B59's P1 pattern closely, so the risk is a missed check, not new design.

## 13. P2 plan: licence, entitlements and billing for a shop (2026-10-20, PLAN ONLY, nothing built)

Read of fleek-backend billing code: `org_subscriptions` (one row per org: plan name, price, status, billing mode, mandate), `compute_org_charge`
(base plan price + in-force priced add-ons), `get_org_access` (tier `trial` | `subscribed` | `free`), `billing_policy` (7-day activation window,
manual mode exempt, enforcement of the end date is OFF by default), `start_checkout` (creates a Razorpay subscription and returns a hosted
`short_url`), and CRM-only orgs (the precedent: `product_mode` plus a plan from a code catalogue `CRM_PLANS`).

### 13.1 Deviation from section 4 (P1/P2 text), flagged
Sections 4 and 11 said a `metal_erp` module row in `org_addons`. The read shows that is the wrong shape for a shop's **base** licence. A shop is
like a CRM-only org: its base licence is the **plan on its `org_subscriptions` row**, drawn from a new `METAL_ERP_PLANS` catalogue. Every billing
rule then works unchanged (trial, mandate, grace, past due, manual mode, plan change, reminders). The CRM core stays what you decided: a separate
add-on row, sold on top (the existing `crm` row). No `org_addons` change for `metal_erp`.

### 13.2 What a shop's licence is
| Part | Where | Notes |
|---|---|---|
| Plan (base) | `org_subscriptions.plan_name` in `METAL_ERP_PLANS` | price, users, limits; catalogue in code first (like `CRM_PLANS`), admin-editable later if wanted |
| Status and access tier | `org_subscriptions` + `get_org_access` | trial, subscribed, free (lapsed); same rules as every org |
| Billing mode | `autopay` or `manual` | existing shops start `manual` (see 13.6) |
| CRM core | `org_addons` row `crm` + tier | optional, separate price, existing grant route |
| Limits for Metal ERP | derived from the plan | users, marketing messages per month (caps quality risk; Meta bills the shop directly), later features |

### 13.3 Entitlements API (Metal ERP asks Fleek)
- `GET /api/internal/metal-erp/orgs/<fleek_org_id>/entitlements` in fleek-backend, signed by Metal ERP with the same HMAC scheme in the
  other direction (Fleek verifies; unset secret = 503). Needs `X-Fleek-Timestamp` and `X-Fleek-Signature`.
- Answer (additive-only, versioned): `{version, org_id, status, access_tier, trial_days_left, plan: {key, label}, limits: {users,
  campaign_monthly_cap, whatsapp_numbers}, modules: {crm: null | {tier, limits}}, billing_mode, issued_at, valid_until}`.
- `valid_until` is how long Metal ERP may rely on the answer without asking again (default 24 h, then the grace in 13.5).
- Answers are read-only and carry no billing detail (no amounts, no mandate data).
- Later, for on-prem boxes that must not hold the shared secret: sign the answer with an asymmetric key (as the Tally agent releases already are)
  and let the box verify it with a public key. Not needed while Metal ERP runs on the Fleek VPS.

### 13.4 Metal ERP side
- New table `tenant_entitlement` (tenant, payload JSON, last_verified_at, valid_until, last_error) and a small client plus one gate function
  (the single answer to "may this firm do X"), the same pattern as `has_module()`.
- A firm with no `fleek_org_id` is **unlimited as today** (fail open): linking is the switch, so no live shop changes until you link it.
- Refresh on a 15-minute TTL and after a plan change; a failed refresh keeps the last good answer until `valid_until` plus grace.
- First enforcement points, kept small: user count when the operator adds users, and outbound WhatsApp and campaigns when lapsed. Everything
  else stays as it is.
- A banner field on the existing `/auth/me` for trial days left and a lapsed notice (UI mockup first).

### 13.5 Decisions needed from you
1. **What "lapsed" means in Metal ERP.** Recommended: never block billing, viewing or PDFs; when the licence has lapsed, stop outbound
   WhatsApp, reminders and campaigns and show a banner; a longer lapse can tighten later. Fleek's own advisor product goes read-only instead.
2. **Offline grace** after a failed refresh (recommended 7 days).
3. **Plans, prices and user limits** for `METAL_ERP_PLANS`, and the monthly marketing-message cap per plan.
4. **Trial length** for a new shop (Fleek default is 15 days) and whether Fleek-assisted shops start in trial or `manual`.

### 13.6 Billing paths (the catch: shop owners cannot sign in to Fleek until P6)
Autopay needs the payer to authorise a mandate on Razorpay, and today that happens on the Billing page behind an org-admin login. Options:
- **A. Manual mode (recommended for the pilot):** Fleek invoices offline and flips status by hand. No code beyond the plan row. Existing shops
  are backfilled this way, so nothing changes for them.
- **B. Hosted authorisation link:** a Fleek admin action calls the existing `start_checkout` and sends the Razorpay `short_url` to the
  shop owner (WhatsApp or email). No login needed, mandate and webhook flow unchanged. Small new admin route.
- **C. Billing page inside Metal ERP:** only after the login phase.
Recommend A now, B once the pilot shop is ready to pay, C with P6.

### 13.7 Build slices (one at a time; none started)
| Slice | Repo | What |
|---|---|---|
| P2a | fleek-backend | `METAL_ERP_PLANS`, `compute_org_charge` and plan-tier lookups for a `metal_erp` org, admin route to give a shop its plan (trial or manual), subscription row; tests |
| P2b | fleek-backend | entitlements endpoint plus inbound HMAC verification; tests including a leak test that one org cannot read another's |
| P2c | Metal ERP | `tenant_entitlement`, client, gate, user-count enforcement, `/auth/me` field; tests |
| P2d | fleek-backend | admin "send authorisation link" route (option B) |
| backfill | both | dry-run then apply: link each live shop and give it a `manual` subscription; you confirm per shop |

### 13.8 Risks
- A missed place that assumes every org has a Planning plan (the access-tier code returns the free tier for an org with no subscription row, which
  would read as lapsed): the metal_erp org must always get its row at creation.
- Lapsed handling in Metal ERP touches sending paths that already carry cost and consent rules; keep the gate in one function.
- Two catalogues of plans (CRM and Metal ERP) in code: acceptable now, revisit if more products arrive.

### 13.9 Build status (2026-10-20, uncommitted)
- **P2a, P2b, P2d built on the Fleek side** (spec `fleek-backend/Plan/features/B60-metal-erp-org-control-plane-p1.md`): plan catalogue (admin-editable
  through Plan Templates), shop creation with plan and trial, the entitlements endpoint, the hosted payment link. Verified on an isolated schema
  (22 DB tests) and 56 pure tests.
- **P2c built on the Metal ERP side:** migration `0058` (`tenant_entitlement` cache), `app/services/entitlements.py` (signed GET, cache until
  `valid_until`, 7-day grace, 5-minute retry backoff, never blocks when unverified), user limit enforced when the operator adds a user, payment
  reminders paused (kept as `failed`, retried after renewal) and auto-send skipped when the licence is lapsed, `licence` field on `/api/auth/me`
  for the banner. Invoice and order messages are NOT gated (lenient rule). 11 tests; full suite green except the pre-existing date-dependent
  reminders test.
- **Still to do:** infra variables (`CONTROL_PLANE_URL=http://fleek-backend:8100` and `CONTROL_PLANE_SECRET` on metalerp-api; `METALERP_CONTROL_PLANE_URL`
  and `METALERP_CONTROL_PLANE_SECRET` on fleek-backend, same value), the banner UI in the SPA (mockup first), the backfill of live shops (link and a
  manual-billing subscription, dry run first), sending the payment link by email or WhatsApp, annual billing.

### 14. P3 and P4 build status (2026-10-20, uncommitted)
Decisions (owner): templates are created once in Fleek and copied to a shop's WABA when it connects; coexistence is the default (Fleek Fintech has it
on); Fleek-assisted connect; templates are finalised at the end, ongoing. Detail: `fleek-backend/Plan/features/B60-metal-erp-org-control-plane-p1.md`.
- **Revision to section 4 (P3):** no new connection table. The number is stored on the shop's owner member row (`advisor_whatsapp_connections`), so the
  existing Embedded Signup flow, webhook subscription and template and Flow copies are reused. The connect flow was split into `connect_number()` and an
  admin route runs it for the shop (`POST /api/admin/orgs/<id>/whatsapp/connect`, plus disconnect, sync, status).
- **Metal ERP side:** `PUT /api/internal/control-plane/whatsapp` keeps Metal ERP's `tenant_whatsapp_config` in step (refuses a number another active firm uses).
- **Webhooks:** a shop's number is routed by owner. Its customers' messages skip Fleek's staff inbox, advisor STOP handling and the auto-responder; Metal ERP
  receives only its shops' events and the older shared-number events (re-signed with the App Secret).
- **Templates:** audience `metal_erp` (migration `b109`); the six Metal ERP templates are adopted from Fleek's WABA in the existing WhatsApp Templates console (Sync from Meta, audience "Metal ERP shops"; no separate script). Document headers and carousels (2-10 picture cards) are adoptable and are re-submitted with fresh sample handles. Carousels are
  created in WhatsApp Manager and adopted; there is no builder yet. Sending a carousel is P5.
- **Verified:** 11 new DB tests, 15 new pure tests, regression 243 of 247 (the 4 are explained in the B60 spec). Metal ERP suite green apart from the
  old date-dependent reminders test.
- **Needs you:** run the adopt script (dry run, then `--execute`) on the VPS; the admin screen for the connect popup needs a mockup; confirm which Meta
  webhook fields are subscribed (`smb_message_echoes`, `history`, `smb_app_state_sync`, `account_update`, `user_preferences`, `business_capability_update`).

## 15. P5 plan: catalog campaigns through the Fleek CRM (2026-10-21, PLAN ONLY, nothing built)

Read of the CRM code (`crm_campaigns`, `crm_outbound.send_engagement_whatsapp`, `crm_whatsapp_templates`, `crm_eligibility`, `crm_consent`,
`whatsapp_outbound.post_template_by_key`). What it gives us for free: the eligibility gate (marketing needs a granted permission, STOP and complaints
block), frequency guard, daily caps, paced batch worker (cron), delivery tracking, results, Meta usage counts, STOP and `user_preferences` handling.
What it does not do yet, and P5 adds:

| Gap | Today | P5 |
|---|---|---|
| Contacts | `crm_contacts.source` CHECK has no Metal ERP value; contacts come from imports, forms, inbound | an internal, signed endpoint where Metal ERP pushes its customers; new source `metal_erp`, external id kept |
| Consent | CRM records consent per contact, channel, purpose | Metal ERP sends each party's consent (status, time, source: counter tick, order page, STOP); recorded as evidence. Opt-outs and STOPs flow back to Metal ERP |
| Template choice | an org sees only `crm` audience templates (`visible_audiences`) | a shop with the CRM core also sees `metal_erp` templates marked pickable |
| Personal link | per-send link kinds are `booking` and `lead_page` only | a `catalog` kind whose value is the customer's own catalog token (minted by Metal ERP), with `?c=<campaign>` appended for attribution |
| Carousel send | `post_template_by_key` builds header, body, quick replies, top-level URL buttons | carousel components: per card an image link and a button suffix; readiness and slot detection understand carousel buttons |
| Campaign content | a campaign has a template and recipients | cards (image URL, title) and body variables stored on the campaign |
| Who starts it | an advisor in the CRM app | pilot: a Fleek admin route for the shop (`POST /api/admin/orgs/<id>/campaigns/catalog`); a screen in Metal ERP later |
| Cap | CRM plan caps | also the Metal ERP plan's `campaign_monthly_cap`, enforced by that route |

### 15.1 Design choices (recommended; need your confirmation)
1. ~~Only consenting customers leave Metal ERP.~~ **Owner 2026-10-21: no such criteria.** Every active customer with a phone is pushed, with whatever
   consent is known (opted in, opted out, none). The CRM's gate alone decides who may be messaged, so someone with no consent is never sent to. The
   DPDP position on customer data reaching Fleek's CRM (section 7, item 6) therefore stays an open item for you.
2. **Metal ERP mints each recipient's personal catalog token** at sync time and sends it with the contact; Fleek stores it against the contact and
   builds the button value `<token>?c=<campaign id>` at send. Metal ERP records the campaign on an order from that query value.
3. **Opt-outs flow back automatically.** When a contact says STOP or Meta reports a stop, Fleek tells Metal ERP, which sets the party's consent to
   opted out. No customer can be messaged by one system after opting out in the other.
4. **Pilot run by Fleek** through the admin route, no shop screen yet.
5. **Card images are hosted by Metal ERP** (public URLs Meta can fetch), supplied with the campaign.
6. **The shop must hold the CRM core** (sold separately). For the pilot Fleek grants it on a manual plan.

### 15.2 Build order (each slice verified before the next)
- **P5a (Fleek):** migration for the `metal_erp` contact source, an external-link table (`org`, `contact`, `system`, `external id`, `link token`), and the
  consent-evidence sources; signed endpoint `PUT /api/internal/metal-erp/orgs/<id>/contacts` (batch, idempotent on external id); consent recorded through
  `crm_consent`; `visible_audiences` for a shop. Tests include: a party without consent is never accepted, a re-push never overrides an opt-out.
- **P5b (Metal ERP):** personal link per party (`catalog_share_link` with a party and a campaign), push of consented parties, consent callback endpoint,
  campaign attribution on orders, a `catalog` consent read for the push. Tests.
- **P5c (Fleek):** carousel send in `whatsapp_outbound` (per-card image and button suffix), `catalog` link kind in `crm_whatsapp_templates`
  (slots, readiness, `link_values`), cards on the campaign.
- **P5d (Fleek):** the admin campaign route (create and start through `crm_campaigns`, plan and monthly-cap checks, results), stop reasons back to Metal ERP.
- **P5e (Metal ERP):** consent capture screens (counter tick, order-page checkbox, STOP), banner and a campaign screen over the CRM APIs (mockups first).
- **P5f:** one real send to the pilot shop's own phone, then to its consented customers.

### 15.3 Risks
- The campaign tables are built around the introduction flow (follow-up steps, wording versions); engagement campaigns already exist (B52q), so P5 extends
  that path rather than adding a new one, but every change touches code with deep tests. Regression runs are long (about 25 minutes per batch).
- Meta rules: marketing templates are reclassified or limited by quality; carousel cards need an image on every card, all the same aspect ratio.
- Two consent stores until the callbacks are proven: the sync must be idempotent and an opt-out must always win.

### 15.4 Build status (2026-10-21, uncommitted)
Owner answers 2026-10-21: no consent criteria on who is pushed; Metal ERP mints the personal token; opt-outs flow back automatically; Fleek runs the pilot.
- **P5a (Fleek), verified:** migration `b110` (contact source `metal_erp`, `crm_contact_external`), `services/metal_erp_contacts.py`, signed
  `PUT /api/internal/metal-erp/orgs/<id>/contacts` (500 per call, only for a shop that holds the CRM core), shop sees `metal_erp` templates in the CRM library.
  Consent rules: an opt-out always wins, Metal ERP can never lift a refusal, `none` never downgrades. 12 DB tests, pure tests.
- **P5b (Metal ERP), verified:** migration `0059` (`party.link_token`, `customer_order.campaign_ref`), `app/services/crm_sync.py` (push in batches, consent mapping,
  `blocked_by_refusal` becomes an opt-out here), `PUT /api/internal/control-plane/consent` (opt-out only), `POST /api/internal/control-plane/contacts/sync`,
  personal link on the public page and orders (`?p=` greets and ties the order to the customer, `?c=` records the campaign; the phone may be left out), and the
  SPA passes `p` and `c` through (type-checks; NOT browser-verified). 11 tests.
- **Opt-out back to Metal ERP:** a STOP or "No" in the CRM for a Metal ERP customer now calls Metal ERP (best effort; the next push reconciles anything missed).
- **Next:** P5c carousel send (campaign payload, `catalog` link kind, carousel components), P5d admin campaign route, then consent capture screens (P5e) and the real send (P5f).

### 15.5 P5c and P5d built (2026-10-21, uncommitted)
- **Carousel send (Fleek):** an adopted carousel's card buttons ending in `/c/{{1}}` become `catalog` slots; each card gets a picture and the customer's own link
  `<catalog link>?p=<customer token>&c=<campaign id>`; a card count mismatch, a non-https picture or a missing token means the message is not sent.
- **Admin campaign route (Fleek):** `POST /api/admin/orgs/<id>/campaigns/catalog` assembles the campaign (CRM core held, WhatsApp connected, ready adopted carousel, pictures
  match cards, customers pushed first, the plan's monthly allowance checked) and the CRM's own paced worker sends it. Results at `GET .../campaigns/<id>`.
- **Verified:** 6 DB tests from the route to the message handed to Meta (consented customers only, personal links, cap, refusals, start/pause/resume/cancel).
- **To run the pilot:** (1) create the carousel template in WhatsApp Manager on Fleek's WABA, wait for approval, adopt it with audience `metal_erp` and mark it pickable
  (`PUT /api/admin/whatsapp-templates/<key>/crm`, kind `engagement`); (2) connect the pilot shop's number; (3) grant the shop the CRM core; (4) link the shop and set the
  Metal ERP env variables; (5) create the campaign with the shop's catalogue link token and card pictures hosted where Meta can fetch them.
- **Still open:** P5e (consent capture and a campaign screen in Metal ERP, mockups first), P5f (the real send), the DPDP position, the infra variables, commits and deploy.

## 16. One console: Fleek manages the whole shop (2026-10-21, backend built, UI awaiting review)

Owner decision 2026-10-21: no second management console. Fleek's admin is the only place an operator manages a Metal ERP shop; Metal ERP's own operator
console is retired in steps. Login stays in Metal ERP (section 4, P6), so shop users are managed by Fleek **through Metal ERP's own admin functions**, over the
same signed channel, with no shared tables.
- **Metal ERP (built, tested):** `app/routers/control_plane_firm.py`, signed routes addressed by the Fleek organisation id: firm details and switches (GST, inward
  import, supplier catalogues, automatic reminders), users (list, add, change role or password, switch off), Tally agent (read, provision, rotate key, pin version,
  installer download), WhatsApp test send. Each calls the operator router's own function, so the plan's user limit and the last-owner guard are the same rules.
  `GET /api/internal/control-plane/unlinked-firms` lists live firms that belong to no organisation. The operator console's WhatsApp paste-in form now refuses a
  firm linked to Fleek (409), so it cannot overwrite what Fleek pushed.
- **Fleek (built, tested):** `routes/admin_metal_erp_settings.py` proxies each of those under `/api/admin/orgs/<id>/metal-erp/...` with an audit entry per action
  (field names and ids only; a password is never logged or recorded). Metal ERP's own refusal (a plan limit, a duplicate email) comes back with its sentence and status.
  Adoption: `GET /api/admin/metal-erp/unlinked-firms`, and `POST /api/admin/orgs/metal-erp` accepts `existing_tenant_id` to link a live firm instead of creating
  one (a failed link is retried with `POST .../metal-erp/provision` plus the same id).
- **Mockup updated** (`fleek-backend/debug_tools/debug_b62_metal_erp_admin_mockup.html`): a Settings tab on the shop page (firm and switches, people with the plan's user count,
  Tally agent) and an "Adopt a live Metal ERP shop" mode on the New shop screen. Not approved; no UI is built.
- **Remaining steps to retire the Metal ERP console:** (1) approve and build the Fleek screens; (2) adopt every live shop (it is a plain step per shop: pick the firm,
  choose a plan and manual billing); (3) then close the console in Metal ERP in stages: firm creation first (an unlinked firm has no licence), then users, switches and Tally,
  finally the sign-in for operators. A setting that switches each stage off is not written yet.
- **Not covered by this move:** the shop owner's own settings inside Metal ERP (logo, bank details, order minimum, terms, reminder days) stay with the shop.

### 16.1 Templates and webhooks: no second entry point (owner 2026-10-21)
Fleek already has one established place to configure and deploy WhatsApp templates and Meta webhooks (the WhatsApp Templates console, Sync from Meta, the rollout
views, the webhook health card). Metal ERP uses that place and adds none:
- the audience `Metal ERP shops` is a new option in the existing console (frontend `whatsappTemplateAudience.ts`, backend `AUDIENCES`);
- the six templates and the carousel are adopted, marked pickable and watched there; copying to a shop's number is the existing push on connect;
- the shop page in the Fleek admin shows the connection (number, mode, whether Metal ERP has been told), not template or Flow status;
- Meta's webhook stays one URL with one health card; the routing by number lives inside the existing handler.
Removed for this reason: the adoption script, and the template and Flow results from the shop's WhatsApp status and sync routes.

### 16.2 The WhatsApp connect flow is a replica of Fleek's advisor flow (owner 2026-10-21; supersedes sections 9, 10 and the P3 notes above)
Rule: Metal ERP copies how Fleek already does a thing. The full comparison is `docs/PARITY-whatsapp-connect.md`.
- **Who connects:** the shop owner, from Metal ERP, exactly as an advisor does from Fleek: connect, retry registration, disconnect. A Fleek admin only SEES the
  connection (`GET /api/admin/orgs/<id>/whatsapp`, read only). There is no admin connect, no admin disconnect and no admin resync, because Fleek has none for advisors.
- **Routes:** Metal ERP `/api/whatsapp-integrations` (GET status, POST connect, retry-registration, disconnect), owner only. Each is a signed pass-through to Fleek's
  `/api/internal/metal-erp/orgs/<id>/whatsapp-integrations/...`, which calls the SAME functions as the advisor routes (`status_for`, `connect_number`,
  `retry_registration_for`, `disconnect_for` in `routes/whatsapp_integrations.py`). Fleek's bodies and statuses pass through unchanged.
- **The only differences from an advisor:** (1) the caller is Metal ERP's server, signed, because an owner has no Fleek session; (2) the status and connect answers also carry
  `connection` (the number's ids) for Metal ERP's own server.
- **The number in Metal ERP is a cache** (`tenant_whatsapp_config`, as `tenant_entitlement` is for the licence): rewritten from Fleek after every call; a number is active
  only when Fleek reports it connected AND registered. Fleek never pushes it; the push route, the admin resync and the pending state are removed.
- **No new configuration:** the page uses the same public ids as Fleek's (build vars `VITE_WHATSAPP_APP_ID`, `VITE_WHATSAPP_CONFIG_ID`); there is no signup-config route.
- **Left out because they belong to the CRM core:** the business-profile card, "What happens next", the org-shared number.
- **Open:** Fleek's number cap (`whatsapp_connect_block_reason`) applies only to CRM-only orgs, so a shop's `whatsapp_numbers` limit is not enforced until `metal_erp` is added there.
  The Metal ERP web page is not built (it is a port of `WhatsAppIntegration.vue`).

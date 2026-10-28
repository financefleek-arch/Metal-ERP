# Onboarding, sign-up and account security (Play Store launch)

Written 2026-10-28. **BUILT the same day, uncommitted** (see section 8 for what differs from this plan). Read before any change to `routers/auth.py`, `AuthPage.tsx`, `lib/auth.tsx`, the Firm page, or a new "Setup" screen.
Rule applied: Metal ERP copies Fleek; the parity table (section 4) is reviewed before any code. Rows marked **NO FLEEK COUNTERPART** need sign-off or are cut.

## 0. Decisions (owner, 2026-10-28)

1. Free sign-up is **open, no admin approval**. Email and mobile are both verified before the firm exists.
2. WhatsApp code uses Fleek's existing AUTHENTICATION template **`advisor_login_otp_v2`** (the one `routes/auth_otp.py` and the client portal already send, context `{code}`, from the shared sender `WHATSAPP_SENDER_NUMBER`). **No new Meta template.**
3. **CAPTCHA at sign-up** (and on "send code" / forgot password). Fleek has none, so this is new; recommended Cloudflare Turnstile (free, works inside the Android app's browser view).
4. "Sign out of other devices" **kept**.
5. **Change email / change mobile cut.** Only change password (plus forgot password) is in scope.
6. **Free plan = one user (the owner), unlimited invoices, invoice creation with parties and items, and recording payments (owner 2026-10-28).** Slip (photo) capture, voice capture and every other add-on are off.
7. **Existing firms are not touched.** No prompt, no verification requirement, no migration of behaviour; they sign in and work exactly as today. Only new sign-ups go through the new flow.

## 1. What is wrong today (read from code, 2026-10-28)

| # | Finding | Where | Why it matters |
|---|---|---|---|
| 1 | `POST /api/auth/register` is public, takes firm + email + password, creates a tenant and seeds ~85 groups. No email check, **no phone collected at all**, no rate limit, no bot check | `routers/auth.py:26` | Anyone can mass-create firms and fill the DB. Email can belong to someone else. |
| 2 | A self-registered firm has **no `fleek_org_id`**, so it has no plan, no limits, no licence, and Fleek admin cannot see it. Entitlements "fail open" for unlinked firms | `entitlements.py`, `register()` | This bypasses the control plane entirely. It is the biggest design gap, bigger than the missing verification. |
| 3 | `/auth/login` has no throttle or lockout. The in-process `RateLimiter` exists but is used only on public order/catalog pages | `services/ratelimit.py` | Password guessing is unbounded. |
| 4 | The JWT lives 12 h and cannot be revoked. `get_current_user` checks only `is_active`. Changing a password, or Fleek admin resetting one, does not end existing sessions | `deps.py:27`, `security.py:43` | A stolen phone or token keeps working. |
| 5 | No change password, no forgot password, no change email/phone, no "sign out everywhere". Password reset exists only as a Fleek-admin action (`control_plane_firm.py`) | whole app | A shop owner who forgets the password must phone Fleek. |
| 6 | Fleek admin sets a password and tells the owner out of band; nothing forces them to replace it | `control_plane_firm.py` | Shared temporary passwords live forever. |
| 7 | Password rule is length 8 only (Fleek requires a digit or special character) | `schemas.py:72` | Weaker than the rest of the stack. |
| 8 | No outbound email code at all (`brevo_api_key` is configured but unused). WhatsApp goes through Fleek (`fleek_messages.py`) | `config.py`, services | Verification and reset need a sender; reuse Fleek's, do not add a second. |
| 9 | After sign-up the user lands on an empty invoice list. No guidance, no sense of what is free versus extra | `App.tsx` | Poor first run; Play reviewers and new shops will bounce. |

Not a gap: the `/api/internal/*` routes are signed (fail closed); the public `/c/` and `/o/` routes are token-based and rate limited. A full "which routes need no login" audit (every router, one table) is step S0 below, because "the APIs are open" should be a verified statement, not a belief.

## 2. Principles

1. **Every firm has a Fleek org from its first second.** There is no such thing as an unlinked self-registered firm. Sign-up creates the Fleek org (plan = Free) and the tenant together, or neither.
2. **Fleek owns identity of the org and the plan; Metal ERP owns the user and the password** (unchanged until P6 login move). All account-management screens in Metal ERP act on the user's own account only. Anything about the org, plan, add-ons, users of other people stays in the Fleek admin.
3. **Verify before you create.** A sign-up request is a pending row that holds no tenant, no seed data, no token. The firm exists only after the owner proves they control the mobile and the email.
4. **One mechanism for every secret-proving step** (sign-up, forgot password, change email, change phone): a short-lived one-time code, hashed at rest, attempt-capped, rate-limited. Written once, used four times.
5. **Free means billing, fully.** Upgrades are requested, not bought in-app (billing UI waits for P6; see 13.6 of the control-plane plan, option A/B).
6. **The checklist is derived from real data, never ticked by hand.**

## 3. Target design

### 3.1 Sign-up (replaces `/auth/register`)

```
SPA                      Metal ERP                           Fleek (control plane)
 form: firm, your name,  POST /api/auth/signup/start
 mobile, email, password   verify CAPTCHA token (Turnstile siteverify)
 + CAPTCHA                 validate, rate-limit (IP + mobile + email)
                           store signup_request (hashed codes,
                           password hash, 30 min expiry)
                           send WhatsApp code  ───────────►  new signed courier route, sends
                                                              advisor_login_otp_v2 {code}
                           send email code     ───────────►  new signed courier route, Fleek email service
 enter both codes        POST /api/auth/signup/verify
                           codes ok, attempts <= 5
                           ───────────────────────────────►  POST /api/internal/metal-erp/self-signup
                                                              create org (firm), owner contact,
                                                              plan = metal_erp_free, event emitted
                           ◄───────────────────────────────  fleek_org_id
                           create_firm(..., fleek_org_id)     (the one provisioning function)
                           create owner user (verified flags)
                           issue token
 lands on Setup screen
```

- Idempotent on `signup_request.id`: a retry after a Fleek timeout returns the same org, never a second one. If Fleek is unreachable the user sees "try again in a minute" and the request stays pending; **no unlinked firm is ever created as a fallback**.
- Duplicate checks: email (as today, case-insensitive) **and** mobile (normalised by the shared `normalizePhone`/`validate_phone` pair). A clash answers "account exists, sign in or reset password" without saying which field matched.
- Password policy = Fleek's: 8+ characters, at least one digit or special, plus a small denylist (top common passwords, email local part, firm name).
- Terms/privacy accepted with a stored timestamp and version (Play and DPDP need this on file).
- Bot control: CAPTCHA token checked server-side before any code is sent (stops WhatsApp-cost abuse, since each code is a paid Meta message), plus per-IP and per-mobile throttles. A failed or missing token is a 400 and sends nothing. Siteverify keys: site key is a public SPA build var, secret key a Metal ERP env var (3 places per infra rule). If Turnstile is unreachable, fail closed.

### 3.2 Sign-in

- Email + password stays. Add: DB-backed throttle per (email) and per (IP) with lockout after 5 failures for 15 min (Fleek's `reserve_attempt` pattern, not the in-process limiter, because production runs several workers); the same generic message for wrong email and wrong password; audit row for lockouts.
- Optional later: WhatsApp OTP sign-in for counter staff (Fleek has `/api/login/whatsapp/*`). Not in this plan; the one-time-code service in 3.4 makes it a small addition.
- Sessions become revocable: `app_user.token_version` (int) goes in the JWT as `tv`; `get_current_user` already loads the user, so the check is free. Bump on password change, "sign out everywhere", admin reset, disable. Lifetime stays 12 h (shop day), no refresh token needed.
- `must_change_password` flag on the user: set when Fleek admin sets or resets a password; sign-in succeeds but the SPA takes the user straight to "choose a new password" and nothing else loads until done.
- A disabled tenant (Fleek suspends the org) must refuse sign-in with a plain message; today only `user.is_active` is checked.

### 3.3 Account screen ("My account", one page, reachable from the header menu)

| Section | Does | Notes |
|---|---|---|
| Profile | Your firm, mobile and email shown read-only with a "Verified" tick | Firm details stay on the Firm page. Wrong mobile or email: ask Fleek (admin edit), by design |
| Password | Change password (current + new + confirm), strength hint | Bumps `token_version`, keeps this session |
| Sessions | "Sign out of all other devices" | Bumps `token_version`, re-issues this device's token. A per-device list is deliberately out (no device table) |
| Forgot password (sign-in page) | Enter email or mobile, get a code on the verified channel(s), set new password | Same response whether or not the account exists |
| Delete account | Starts the existing delete-account request | Play requires an in-app path; the public page already exists |

Roles: any user manages their own account; the owner additionally sees "who else has access" read-only with "ask Fleek to add or remove people" (account management stays in Fleek admin).

### 3.4 One-time code service (new, small, reused four times)

`otp_challenge(id, purpose, channel, target, code_hash, expires_at, attempts, consumed_at, ip)` with purposes `signup` and `reset` (a third, e.g. WhatsApp sign-in, is a later addition). 6 digits, 10-minute life, 5 attempts, 60 s resend gap, 5 sends per hour per target. Delivery is via Fleek (see below). **Metal ERP generates, hashes and checks the code; Fleek is only a courier.** Two new signed Fleek routes, `POST /api/internal/metal-erp/codes/whatsapp` (`{to, code}`, sends `advisor_login_otp_v2` through the same `send_template_message` call `auth_otp.py` uses) and `.../codes/email`. Fleek stores no code, so there is no second OTP table and no shared state. Both routes are throttled on Fleek's side as well (per number, per hour) so a leaked signing secret cannot become a message cannon. Metal ERP never holds a Brevo or Meta credential.

### 3.5 Plan and entitlements: the Free plan

- Fleek: add `metal_erp_free` to `METAL_ERP_PLANS` (admin-editable in Plan Templates, as the other plans). Billing mode `manual`, no trial clock, no payment mandate, no expiry. Fleek admin sees the shop in the existing list with a "self sign-up" source and the event in the audit log.
- **Free includes only:** firm profile (name, address, bank/UPI, terms), parties, items (with the pre-seeded groups), invoice creation, PDF and the phone's own share sheet, **recording payments with the party ledger and bill-wise allocation**. **One user** (the owner); unlimited invoices.
- **Not in Free, enabled per shop by Fleek admin (existing switches and plan limits):** slip/photo capture and voice capture, collections view and reminders, bulk import (Excel/Tally file), WhatsApp from the shop's own number, customer catalogs and ordering, Tally Companion and voucher push, supplier catalogs, inward bill import, extra users, CRM campaigns. Where one of these has no switch today (payments, collections, bulk import, slip, voice) the plan limit payload gets a `features` list and one gate function answers "may this firm do X" (the `has_module()` pattern); this is the only new gating mechanism.
- **Fail-open stays for existing firms:** a firm that was live before this ships keeps every feature it has today. The gate defaults to allowed for any firm created before the cut-over date or already linked, and applies the Free restrictions only to firms whose plan is `metal_erp_free`.
- Lapsed handling is unchanged (never block billing, viewing or PDFs).

### 3.6 Setup screen and checklist (the first-run experience)

A **Setup** page (nav item while incomplete, plus a progress ring in the header and a card at the top of the invoice list), one server endpoint `GET /api/onboarding` that computes state from data:

**Part A: Get invoicing going (Free, always available)**

| Step | Done when | Helper |
|---|---|---|
| 1 Confirm your firm | name, address, state, phone present | opens Firm page section |
| 2 Add bank / UPI and terms | any of bank a/c, UPI present | explains these print on the invoice |
| 3 Add your customers | >= 1 party | button: Add customer |
| 4 Add your items | >= 1 item | explains groups are pre-set; add items |
| 5 Make your first invoice | >= 1 finalized invoice | opens editor |
| 6 Send it | invoice shared/downloaded | PDF + share sheet |
| 7 Record a payment | >= 1 payment | explains the party ledger |

**Part B: Add more (each a card: what it does, who it is for, what to have ready, time to set up, status)**

| Feature | Status values | What the guide says (one screen each) |
|---|---|---|
| Send from your own WhatsApp number | Not on plan / Requested / Needs action / Active | Needs: a number on the WhatsApp Business app, Facebook login, a card for Meta's own charges. Then opens the existing connect card. Reuses `WhatsappSetupChecklist` marks |
| Collections & reminders | Not on plan / Active | Ageing view, daily reminder proposals; sending needs the WhatsApp card |
| Faster entry: bulk import, photo slip, voice | Not on plan / Active | Import items/parties from Excel or Tally; capture a bill from a photo or by speaking; always confirmed before saving |
| Customer catalogs & online ordering | Not on plan / Active | Customers order from a link; needs items with prices and photos |
| Tally connection | Not on plan / Active | Needs Tally Prime on a Windows PC; install the Companion; what is backed up and pushed |
| Supplier catalogs & purchase bills | Not on plan / Active | Upload supplier PDFs, review, push to Tally |
| Team members | Free = owner only | Roles explained (owner, accountant, counter, viewer); "ask Fleek to add" |
| Marketing campaigns (CRM) | Not on plan / Active | Sold separately; consent rule explained |

- Status comes from the same entitlements payload (`modules`, `limits`) plus local config, so there is **one** answer per feature, in one function (the `has_module()` pattern).
- "Ask Fleek to enable" = a button that sends a **request** to Fleek (shop, feature, contact) and shows "Requested on <date>". Fleek admin sees it on the shop page, enables the feature or plan, the next entitlements refresh flips the card. Billing details follow Fleek's existing manual/payment-link route. **NO FLEEK COUNTERPART** for an in-app request button (see table).
- Guides are static content bundled in the SPA (headings, 5-8 lines, a "what you need ready" list, a link to the relevant screen). No CMS, no videos at launch.
- Dismissible per section once Part A is complete; reopened from the header menu. Dismiss state lives on the server (per tenant) so it is the same on web and the Android app.

## 4. Parity table (Fleek source -> Metal ERP), to review before code

Fleek sources read 2026-10-28: `services/signup_service.py` (S), `routes/auth.py` (A), `routes/auth_otp.py` (O), `services/rate_limit.py` (R), `services/email_service.py` (E).

| Element | Fleek | Metal ERP | Match? |
|---|---|---|---|
| Sign-up creates a pending request, account only after verification | `create_signup_request` (S:239), 24 h token, cancels older pending | `signup_request` row, codes not link, 30 min | Same idea. **Difference:** Fleek verifies by email link; we use a code because the Android app cannot reliably catch an emailed link |
| Mobile required at sign-up | S:~290 (since 2026-09-28) | Required and verified | Same, plus verification |
| Email uniqueness by blind index, generic conflict text | S, `ConflictError` | Lower-case compare, same sentence | Same |
| Password rule: 8+, digit or special | S `_PW_STRENGTH_RE` | Same regex | Same |
| Org + owner member created together | `_create_org`, `_create_advisor_row` | Fleek `self-signup` endpoint creates the org; Metal ERP creates the user | Same shape (control-plane section 11) |
| WhatsApp OTP template | `advisor_login_otp_v2`, AUTHENTICATION, `{code}`, shared sender (O:128); client portal reuses it (`client_auth_otp.py:27`) | Same template key, same sender, sent by Fleek on Metal ERP's behalf | Same, nothing new in Meta |
| WhatsApp OTP rules | 5 min, 5 attempts, 15 min lockout, no enumeration on verify (O) | Same numbers, 10 min life (Android paste delay) | Same. **Difference:** Fleek's /send tells an unknown number "no account"; ours stays generic |
| DB-backed attempt reservation | `reserve_attempt` (R) | Same behaviour in Metal ERP's DB (small table), keyed IP + target | Same pattern, own table (no shared tables) |
| Email sending | `email_service.send_*`, Brevo/SES | Request Fleek to send, signed; Metal ERP holds no mail key | Reuse via control plane |
| Forgot / set password via emailed link | `send_advisor_password_email` | Code via WhatsApp or email, then set password | Same flow, code instead of link |
| Admin creates account, user sets own password | org-created email + set-password link | Admin reset sets `must_change_password` | Equivalent |
| Revocable sessions | Fleek sessions table (A `create_session`) | `token_version` on user | Simpler; no session table |
| "Sign out of other devices" | not seen in A | In scope (owner OK'd, 0.4) | **NO FLEEK COUNTERPART (small)**, approved |
| Change mobile/email | not seen | **Cut** (0.5) | n/a |
| CAPTCHA | none found in fleek-backend or fleek-frontend | Turnstile on sign-up, send-code, forgot password | **NO FLEEK COUNTERPART**, approved (0.3) |
| Code delivery courier routes | not seen | `POST /api/internal/metal-erp/codes/{whatsapp,email}` | **NO FLEEK COUNTERPART**; follows the existing `internal_metal_erp.py` signed-route pattern |
| Free plan with no trial clock, self sign-up source | not seen (Fleek self-signup starts a 15-day trial, then approval) | New `metal_erp_free` plan | **NO FLEEK COUNTERPART**; needs Fleek spec |
| In-app "ask Fleek to enable" request | not seen | New signed request + admin list | **NO FLEEK COUNTERPART**; needs Fleek spec |
| Admin approval of new accounts | `approve_advisor` (S:859) | Not copied: Free is open, abuse handled by verification and throttles | Deliberate difference (0.1) |

## 5. Build order (each slice ships alone, behind a switch where it changes behaviour)

| Slice | Repo | What | Notes |
|---|---|---|---|
| S0 | Metal ERP | Audit every router for auth, one table in this doc; add login throttle; reject `register` when `SIGNUP_V2` on | Closes the worst hole immediately |
| S1 | Metal ERP | `app_user` columns (`mobile`, `mobile_verified_at`, `email_verified_at`, `token_version`, `must_change_password`, `terms_accepted_at`), `otp_challenge`, `signup_request`, `auth_attempt`; migration | Existing users: columns default so nothing changes (`token_version` 0, verified flags null, no prompt) |
| S2 | Metal ERP | Code service + throttles + token_version in JWT + must-change-password gate | Pure backend, tests first |
| S3 | Fleek | `metal_erp_free` plan; `POST /api/internal/metal-erp/self-signup`; the two code courier routes (reusing `advisor_login_otp_v2` and a new plain email template); audit event | Spec in `fleek-backend/Plan/features/` |
| S4 | Metal ERP | `/auth/signup/start|verify`, forgot/reset, change password, sign out others, Turnstile check | Old `register` removed once live |
| S5 | SPA | Sign-in/sign-up/forgot screens, My account page (change password only), Turnstile widget, forced password change | **Mockup first** (UI rule) |
| S6 | Metal ERP + SPA | `GET /api/onboarding`, Setup page, guides, request button; Fleek request list | Mockup first |
| S7 | both | **Dropped (0.7):** existing firms are left as they are. Instead: a cut-over date/flag so the Free restrictions and the new sign-up apply only to new firms | No existing-user backfill prompt |
| S8 | docs/Play | Reviewer account created by Fleek admin (password sign-in, no OTP needed); data-safety form and listing updated | `PLAY-STORE-LISTING.md` |

## 6. Still open

- Collections view (ageing) and reminders are outside Free; payments recording and the party ledger are inside.
- Turnstile account and keys (site key public build var, secret server var).
- Wording of the email code message (Fleek brand, short).
- Whether the `reset` flow may send the code to both channels or only the one the user picks (recommended: user picks; both are verified).

## 7. Risks

- **Fleek down at sign-up:** sign-up fails with a retry message, by design (principle 1). Fleek's uptime becomes sign-up's uptime; acceptable because it is the same VPS.
- **WhatsApp code delivery failure** (number not on WhatsApp, Fleek sender number rate-limited): the email code is the fallback and either channel alone can complete reset; sign-up needs both. Tell the user plainly which code did not arrive and offer resend after 60 s.
- **Play review:** reviewers cannot receive OTPs. Mitigation S8 (admin-created reviewer account). Never add a hardcoded OTP bypass.
- **Lockout as denial of service:** locking by email alone lets an attacker lock an owner out. Throttle by (email + IP) and by IP separately, and let the reset flow bypass the lockout since it needs a code.
- **`token_version` check on every request** adds no query (user is already loaded) but any code that decodes the JWT without loading the user must be found (S0 audit).
- **Seeded data at sign-up** (synonyms + taxonomy) is heavy; it now happens only after verification, so abusive sign-ups never reach it.

## 8. Build status (2026-10-28, uncommitted)

Built: migration `0064`; `models/account.py`; services `auth_codes`, `auth_throttle`, `captcha`, `passwords`, `account`, `features`, `onboarding`, `fleek_signed`; router
changes in `auth.py` and new `onboarding.py`; SPA `AuthPage`, `AccountPage`, `SetupPage`, `FeatureRoute`; Fleek B67. Go-live: `docs/GO-LIVE-onboarding.md`.
Verified: 25 new API tests, the full API suite, SPA type-check, lint and build, 35 Fleek pure tests. **Not verified:** the Fleek DB tests, a real WhatsApp/email code,
Turnstile, or the screens in a browser.

Differences from the plan above:
- **Setup has six steps** ("Send it" has no data to tick from, so it is guidance inside "Make your first invoice"). "Record a payment" opens a customer, not Collections, because Collections is not on Free.
- **Free keeps payments and the party ledger; the collections view and reminders are off** (owner, later in the same conversation).
- **One code field, not six boxes**, so the phone can auto-fill it.
- **"A disabled tenant refuses sign-in" was dropped:** a firm has no active flag today, and Fleek's lapsed rule is deliberately lenient. Revisit with the licence rules.
- **Existing firms:** untouched (0.7). The Free limits apply only to a firm whose plan is `metal_erp_free` (or a self-signup firm Fleek has never answered for).
- **Old `/api/auth/register`** stays only behind `REGISTRATION_OPEN` (off in production) because the test suite and developers make firms with it.
- Admin-created users and Fleek/operator password sets now set `must_change_password` and end old sessions.

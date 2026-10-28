# Go-live: verified sign-up, Free plan, My account, Setup

Built 2026-10-28, uncommitted. Plan: `docs/PLAN-onboarding-and-account.md`. Mockups: `docs/visual-plan/Onboarding.html`. Fleek side: `fleek-backend/Plan/features/B67-metal-erp-self-signup-and-free-plan.md`.

## Order (Fleek first, because sign-up calls it)

1. **Fleek:** deploy fleek-backend (self-signup, code routes, feature-request route, Free plan). Run the DB tests listed in B67 first.
2. **Cloudflare Turnstile (no infra change):** create a widget for `metal.fleekfinance.in`, then add the two keys in the Fleek admin **platform secrets**
   screen (category Auth): `TURNSTILE_SECRET_KEY` (secret) and `TURNSTILE_SITE_KEY` (the public key). Metal ERP holds no Cloudflare key: it sends the
   visitor's token to Fleek, and the sign-in page fetches the site key from Metal ERP, which gets it from Fleek (cached 5 minutes).
3. **Do NOT set `REGISTRATION_OPEN`** in production. It is the old one-call, unverified `/api/auth/register`; unset it is a 404.
   With no `TURNSTILE_SECRET_KEY` in Fleek, or Fleek unreachable, production sign-up answers 503 on purpose (fail closed).
4. **Metal ERP:** deploy; `alembic upgrade head` runs migration `0064` (adds columns with harmless defaults and three new tables; existing firms and users are unchanged).
5. **Play Store:** create the reviewer account from the Fleek admin (password sign-in, no code needed). The reviewer will be asked to choose a new password on first sign-in because Fleek set it: either use the app once and give the reviewer the new password, or clear `must_change_password` for that user in the database.

## What changes for people

- **New sign-ups:** mobile (WhatsApp code) + email code + CAPTCHA; the firm is made in Fleek on the Free plan first. Free = one user, invoices, parties, items, payments.
- **Existing firms and users:** nothing is forced. They gain "Forgot password?", "My account" (change password, sign out other devices) and, for owners, a "Setup" entry (their data already ticks most steps).
- **Sessions:** tokens now carry a version. A password change, a reset or a Fleek-admin password set ends other sessions.
- **Fleek-admin password sets and new users:** the person must choose their own password at first sign-in (`must_change_password`).

## Not done / follow-ups

- Terms of service: linked at https://fleekfintech.com/terms/ on the sign-up checkbox (accepted version `2026-10` is stored).
- Fleek: a "Requests" list for feature requests (today they are in the audit log and platform events).
- The Setup card guides are short static text in `api/app/services/onboarding.py`; richer guides can follow.
- Sign-in by WhatsApp code (Fleek has it for advisors) is not offered; the code service makes it a small addition.
- Browser verification of the new screens against a real Turnstile and a real WhatsApp send has not been done (type-check, lint, build and API tests only).

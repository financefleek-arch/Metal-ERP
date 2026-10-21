# Go-live checklist: Fleek control plane, Metal ERP shops, catalogue campaigns

Compiled 2026-10-21 from everything built so far (plans: `PLAN-fleek-control-plane.md`, `PLAN-catalog-campaigns.md`; Fleek spec `fleek-backend/Plan/features/B60-metal-erp-org-control-plane-p1.md`).
Everything is built and tested but **uncommitted and undeployed**. Items marked VERIFY are Meta facts from memory, not yet checked against current Meta docs.

## A. Decisions still needed from the owner
1. **DPDP position:** every active Metal ERP customer with a phone is pushed to Fleek's CRM (owner: no consent criteria). Confirm the shop terms / DPA cover the shop as data owner and Fleek as processor.
2. **Consent wording** for the two capture points (counter tick, order-page checkbox) and whether the order-page box is pre-ticked (earlier decision: default ticked, unticking = opt-out). Hindi / Bengali wording if needed.
3. **Plan prices and limits** (placeholders: Starter 1,999 / Standard 3,999 / Plus 5,999 per month; 3 / 6 / 15 users; 1,000 / 5,000 / 20,000 campaign messages a month).
4. **CRM core price** for a shop (sold separately; for the pilot Fleek grants it manually).
5. **Pilot shop** (name, owner contact email, number on the WhatsApp Business app) and its first campaign audience.
6. **Annual billing:** parked; monthly only for now.

## B. Source control and deploy (nothing is committed yet)
7. **Infra first** (`fleek-infra` repo, then the app repos). **Edits are made in `c:\Fleek Finance Code\infra` but not committed.** They add a Vault-sourced shared secret, so the ORDER matters (a missing KV field aborts every deploy of every service): (a) on the VPS create `secret/metalerp/core#control_plane_secret` with `vault kv patch` (never `put`), using the command in `vault/RUNBOOK.md` section "Control plane shared secret"; (b) then push `fleek-infra`; (c) then the app repos. Variables added, one shared secret value in all four places:
   - fleek-backend: `METALERP_CONTROL_PLANE_URL` (for example `http://metalerp-api:8000`) and `METALERP_CONTROL_PLANE_SECRET` (platform secret or env).
   - metalerp-api: `CONTROL_PLANE_URL` (for example `http://fleek-backend:8100`) and `CONTROL_PLANE_SECRET`.
   - Already present and unchanged: `METALERP_WHATSAPP_WEBHOOK_URL`, `WHATSAPP_APP_SECRET` (the fan-out now re-signs a filtered body with it).
8. **Commit and push** (you commit; I do not): Metal ERP repo; fleek-backend repo (it also holds another session's B61 work in the same tree: review what you are committing); fleek-infra.
9. **Migrations on prod** (applied automatically on deploy; none has run on prod):
   - Fleek: `b102` to `b105` (B59 CRM core), `b106` (metal_erp product mode), `b107`, `b108` (B61, other session), `b109` (template audience), `b110` (CRM contacts from Metal ERP).
   - Metal ERP: `0057` (firm link), `0058` (licence cache), `0059` (party link token, order campaign).
10. **Frontend builds** deploy with each repo. The Metal ERP SPA change (personal link on the public catalogue page) is type-checked only: open `https://metal.fleekfinance.in/c/<token>?p=<token>&c=1` in a browser once.
11. **DNS and TLS** for `metal.fleekfinance.in` (the Caddyfile comment says the A record was still pending): the carousel button opens this host.
12. **Cron:** confirm the `crm-process-sends` job runs every minute (it sends queued campaigns).

## C. Meta / WhatsApp
13. **Meta app (FleekWA):** advanced access and Embedded Signup configuration are in place (Fleek is an approved Tech Provider). Confirm the app's allowed domains include the Fleek admin domain that will host the connect popup (VERIFY).
14. **Webhook fields subscribed** (use the existing webhook health card in the Fleek admin and the existing `check_meta_webhook_subscription` / `check_whatsapp_webhook_subscription` scripts; nothing new is configured) in the Meta App Dashboard: `messages`, `message_template_status_update`, `smb_message_echoes`, `history`, `smb_app_state_sync`, `account_update`, `user_preferences`, `business_capability_update`, `account_alerts`.
15. **Pilot shop's number** is on the WhatsApp Business app (version 2.24.17 or later), has been active for about a week, and is not on regular WhatsApp (error 2494064). The shop's owner is a Facebook admin of their business and has a payment method ready for their WABA (Meta bills the shop directly).
16. **Create the carousel template** in WhatsApp Manager on Fleek's own WABA (see section F for the exact configuration) and wait for approval.
17. **Check the six existing Metal ERP templates** on Fleek's WABA are Approved and match the variable counts (`invoice_ready` 3, `payment_reminder` 3, `account_statement` 2, `order_received` 5, `order_update` 4, `new_order_alert` 4); the adopt script reports any mismatch.

## D. Fleek: one-time data steps (run inside the fleek-backend container after deploy)
18. `python scripts/seed_metal_erp_plans.py` (dry run), then `--execute`: writes the default plans as editable rows. Tune prices and limits in the admin Plan Templates (plan names `metal_erp_*`, `features` JSON: label, users, campaign_monthly_cap, whatsapp_numbers).
19. **In the existing WhatsApp Templates console** (Sync from Meta): adopt the six Metal ERP templates (`invoice_ready`, `payment_reminder`, `account_statement`, `order_received`, `order_update`, `new_order_alert`) with their variable names in order (Metal ERP's `TEMPLATE_BODY_PARAMS`) and the audience **Metal ERP shops**.
20. **Adopt the carousel there too** (name, language `en`, placeholder name `name` if the body greets), audience Metal ERP shops.
21. **Offer the carousel to shops:** mark it pickable as an engagement message (existing control; title `New range`, purpose `Show your catalogue`). Watch approval and the per-number rollout in the same console.
22. **Secrets:** confirm `FLEEK_WABA_ID`, `WHATSAPP_API_KEY`, `WHATSAPP_APP_ID`, `WHATSAPP_APP_SECRET` are set (the sample-upload handle for templates needs the app id).

## E. Pilot shop onboarding (Fleek-assisted)
23. **Create the shop org:** `POST /api/admin/orgs/metal-erp` (shop name, city, contact name, contact email that is not already a Fleek account, optional plan, trial days, billing mode). Fleek creates the Metal ERP firm by a signed call; if it answers `pending`, retry with `POST /api/admin/orgs/<id>/metal-erp/provision`.
24. **Existing live shops:** adopt each one instead of recreating it: `GET /api/admin/metal-erp/unlinked-firms`, then `POST /api/admin/orgs/metal-erp` with `existing_tenant_id`, a plan, `billing_mode: manual` and `trial_days: 0`. Nothing in the firm changes (its data and users stay). A failed link is retried with `POST /api/admin/orgs/<id>/metal-erp/provision` and the same `existing_tenant_id`.
25. **Billing:** trial runs 15 days on autopay. Before it ends, send the owner the hosted authorisation link from `POST /api/admin/orgs/<id>/metal-erp/payment-link` (it is not sent for you), or keep the shop on manual billing.
26. **Connect the number:** the shop owner connects it themselves from Metal ERP's WhatsApp page (a port of Fleek's advisor page; not built yet), as an advisor does in Fleek. A Fleek admin only sees the connection. Same public ids as Fleek's page (`VITE_WHATSAPP_APP_ID`, `VITE_WHATSAPP_CONFIG_ID` on the Metal ERP web build, three places). Add `metal.fleekfinance.in` to the Meta app's allowed domains (the popup then runs on that domain).
27. **Grant the CRM core:** `POST /api/admin/orgs/<id>/crm-core` (tier, price, `apply_now`).
28. **Metal ERP side of the shop:** the firm exists and is linked; set the operator toggles as needed; add the shop's users (limited by the plan); create the catalogue share link the campaign will open (note its token).
29. **Consent capture:** until the screens exist, consent can only be recorded by staff per party (`PUT /api/parties/<id>/catalog-consent`). Customers with no recorded consent are pushed but never messaged.
30. **Card pictures:** one public https image per card, same aspect ratio, hosted where Meta can fetch them (Metal ERP media URLs or object storage; confirm they are public).

## F. The carousel template (what to create in WhatsApp Manager)
| Setting | Value |
|---|---|
| Name | `catalog_range_carousel` (lowercase, digits, underscores) |
| Category | Marketing |
| Language | English (`en`) |
| Type | Media carousel |
| Body | `Hi {{1}}, our new range is here. Swipe through the highlights, then tap View catalogue to see every item with prices and place an order.` Sample for `{{1}}`: `Ramesh`. Adopt it with placeholder name `name` (the CRM fills it with the customer's first name, or "there"). If you prefer no greeting, write the body without a variable and adopt with `[]`. |
| Cards | **4 cards** (fixed in the template: a campaign must supply exactly this many pictures; create another template for a different count, for example `catalog_range_carousel_3`) |
| Card header | Image on every card, same aspect ratio on all (1:1 recommended; VERIFY limits: 1.91:1 or 1:1, file size). The real pictures are supplied at send time; the sample you upload only passes review |
| Card body | Static text, identical or similar on all cards, at most 160 characters (VERIFY), for example `Tap below to browse the full catalogue.` **Put the category name in the picture itself**: the card text is part of the approved template and is not filled per send |
| Card button | One **URL** button per card, text `View catalogue` (25 characters or fewer), type Dynamic, URL `https://metal.fleekfinance.in/c/{{1}}`, sample `https://metal.fleekfinance.in/c/Ab12Cd34Ef56?p=Xy98Zw76Vu54&c=1a2b3c4d`. The CRM sends the suffix `<catalogue link token>?p=<customer token>&c=<campaign id>` |
| Other buttons | None for now (the CRM sends only the catalogue button per card; a second button would need its own send support) |
| Footer | Not available on carousel templates |
After approval, adopt it (step 20); the adopt check requires 2 to 10 cards, picture headers, and a body with a positional variable count equal to the names you give.
Every card opens the **same** catalogue page. Per-card category pages are not built.

## G. First real send (P5f)
31. Run a campaign to the **pilot shop owner's own phone** first: put the owner as the only consented customer, then `POST /api/admin/orgs/<id>/campaigns/catalog` with `template: "wa:catalog_range_carousel"`, the catalogue link token, four card pictures, `start: true`. Check: the carousel renders, each card button opens the owner's personal link (name prefilled, phone optional), an order from it shows the campaign reference.
32. Check delivery and reads in `GET /api/admin/orgs/<id>/campaigns/<campaign id>`, Meta's message counts (the shop is billed by Meta), and that a STOP reply marks the customer opted out in Metal ERP.
33. Then the shop's consented customers, in a small batch first (Meta's per-user marketing cap and quality rating apply; the portfolio starts at 250 unique users a day until verified).
34. **Roll back path:** cancel the campaign (`POST .../cancel`), have the owner disconnect the number on the WhatsApp page, or unlink nothing: every other shop stays on the shared number until moved on purpose.

## G2. One console (Fleek manages the whole shop; section 16 of the plan)
- Backend built: shop settings, users, Tally agent and WhatsApp test are reachable from Fleek through signed routes (`/api/admin/orgs/<id>/metal-erp/settings|users|tally|whatsapp-test`). A linked firm's WhatsApp number can no longer be pasted into the Metal ERP console.
- To retire the Metal ERP operator console: approve and build the Fleek screens, adopt every live shop (step 24), then close the console in Metal ERP in stages (firm creation first). The switch that closes each stage is not written yet.

## H. Not built (will need work before the matching feature is usable)
- Admin screens (Fleek console): new Metal ERP shop, connect number, plan, campaigns. Today these are API calls.
- Consent capture screens in Metal ERP (counter tick, order-page checkbox) and a campaign screen for the shop.
- Cloud-number PIN retry for a shop, and handling of Meta-side disconnects (`PARTNER_REMOVED`).
- Annual billing; sending the payment link by email or WhatsApp; live-shop backfill script.
- Login moving to Fleek (later phase).

## I. Known test gaps to know about
- Pre-existing failing tests unrelated to this work: Metal ERP `test_reminders` (date-dependent), Fleek `test_meeting_context_is_worded_like_the_reminder` (removed module), a CRM consent-wording test from another session.
- The SPA personal-link change is not browser-verified; nothing has been sent to real Meta from this work.

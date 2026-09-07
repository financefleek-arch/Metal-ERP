# FEATURE — WhatsApp Business (per-firm number, invoice send)

Status: **built + committed** on `main` across 4 commits (2026-09-03 → 09-07):
`1d0e6a0` (schema + plumbing + admin panel), `fe6fc32` (test-send endpoint,
config), `5159854` (invoice send + editor button), `3528158` (shared PDF
filename, dialog rework). Migration `0014` applied on prod.

Raw Meta sends verified working (`hello_world` + the `invoice_ready` template
via the `/whatsapp/test` endpoint). The full app path — the "Send on
WhatsApp" button on a real finalized invoice — has **not** been exercised
live yet.

---

## 1. What it does

Each client firm sends its invoices from **its own WhatsApp Business
number**. Messages appear to come from the firm, not a shared Fleek number.

- Operator (platform admin) connects a firm's number in the Operations
  console.
- On a **finalized** invoice, a user clicks **Send on WhatsApp**, picks the
  recipient(s), and the invoice PDF goes out as the `invoice_ready` template.

---

## 2. Architecture

### One Meta app, one WABA, many numbers

```
Business Portfolio: "Fleek Services"        ← Business Verification (done, once)
└── Meta App: "FleekWA"  (ID 1361815816140470)   ← published (done, once)
    └── System User + permanent token         ← WHATSAPP_API_KEY (one secret)
        └── WABA "Fleek Fintech" (1082044344274334)   ← templates approved HERE
            ├── phone_number_id 1323635814156061  (+91 89565 06362)  → firm A
            ├── phone_number_id ...                                  → firm B
            └── ...
```

- **No Embedded Signup / Tech Provider.** The operator manually adds each
  firm's number to the FleekWA WABA in Meta, then registers it (SMS OTP), then
  pastes `phone_number_id` + `waba_id` into the Ops console. Fine for a
  handful of firms; avoids Meta App Review + Tech-Provider Access
  Verification. (fleek-backend's B23 Embedded Signup is the alternative if
  firms ever self-serve.)
- **All firms' numbers go under the same WABA** → `invoice_ready` /
  `payment_reminder` are approved **once**, every number reuses them. Only
  `phone_number_id` routes a send (`POST /{phone_number_id}/messages`);
  `waba_id` is stored per firm but not used for routing.
- **No per-firm token.** `whatsapp_api_key` is app-wide. `tenant_whatsapp_config`
  has no token column — nothing secret at rest in the app DB.

### Secrets

| Name | Value | Used for | Where |
|---|---|---|---|
| `whatsapp_api_key` | FleekWA System User token (`EAA…`, permanent) | Bearer on every Graph API call | Vault `fleek-backend/core#whatsapp_api_key` (shared with fleek-backend). Exported by `fleek-infra/scripts/load-vault-secrets.sh` as `METALERP_WHATSAPP_API_KEY`; `docker-compose.yml` maps it to `WHATSAPP_API_KEY`. **Done.** |
| `whatsapp_app_secret` | FleekWA **App Secret** | Inbound webhook only: HMAC key for `X-Hub-Signature-256` **and** the GET-handshake verify-token (one value, both jobs). fleek-backend uses the same App Secret for its own webhook HMAC. | Vault `fleek-backend/core#whatsapp_app_secret` (exists). **NOT yet exported/mapped for metalerp-api** — see §7. Sends work without it; only delivery/read receipts need it. |
| `whatsapp_api_version` | `v25.0` | Graph API path | `config.py` default (matches fleek-backend's tested value; older versions expire). |

`config.whatsapp_configured` = `bool(whatsapp_api_key)` — only the token is
required to send.

---

## 3. Data model — migration `0014`

- **`tenant_whatsapp_config`** — one row per firm. `tenant_id` (unique),
  `phone_number_id`, `waba_id`, `display_phone_number`, `is_active`. No token.
- **`party.whatsapp_optin`** — BOOLEAN default false.
- **`whatsapp_message`** — outbound log. `tenant_id`, `party_id` (nullable),
  `invoice_id` (nullable), `template_name`, `to_phone`, `media_id`,
  `wa_message_id`, `status` (`pending`→`sent`→`delivered`→`read` / `failed`),
  `error`, `sent_at` / `delivered_at` / `read_at`.

---

## 4. API

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/admin/firms/{id}/whatsapp` | PlatformAdmin | read a firm's WA config |
| `PUT /api/admin/firms/{id}/whatsapp` | PlatformAdmin | upsert `{phone_number_id, waba_id, display_phone_number?, is_active}` |
| `DELETE /api/admin/firms/{id}/whatsapp` | PlatformAdmin | remove |
| `POST /api/admin/firms/{id}/whatsapp/test` | PlatformAdmin | **test send** — `invoice_ready` + dummy params + a hand-built throwaway PDF (no invoice needed). Body `{to_phone, template_name?, with_document?}`. |
| `POST /api/invoices/{id}/whatsapp` | WriteUser (tenant-scoped) | send this finalized invoice's PDF. Body `{template_name?="invoice_ready", to_phone?}`. `WhatsappError`→502 (Meta's message), `WhatsappNotConfigured`→422. |
| `GET /api/whatsapp/webhook` | none | Meta subscription handshake (`hub.verify_token` = app secret) |
| `POST /api/whatsapp/webhook` | none (HMAC) | status callbacks. Fail-closed in prod: no app secret → 503. |

### `send_invoice(session, invoice, *, template_name, to_phone=None)`

- `to_phone` **given** → send there; **skip** the `party.whatsapp_optin` +
  `party.phone` guards (deliberate operator one-off). Party name in the
  message still from the invoice's party (`"Customer"` if the invoice has
  none).
- `to_phone` **omitted** → party path: party must exist, be opted in, have a
  phone.
- Always: invoice must be `final`; firm must have an active
  `tenant_whatsapp_config`; PDF taken from `invoice.pdf_path` and uploaded to
  `/{pnid}/media`, referenced in the template's **document header**;
  `whatsapp_message` row recorded.
- Attachment filename = `download_name(invoice)` — the same
  `<Party> <YYYY-MM-DD> <total>.pdf` the browser download uses
  (`services/invoices/common.py`, single source of truth).

---

## 5. UI

- **Operations console → firm detail → "WhatsApp" section**
  (`web/src/pages/admin/FirmsPage.tsx`): phone_number_id / waba_id / display
  fields, Active toggle, Connect / Save / Remove, status pill (not connected /
  connected / paused), and a **"Send a test message"** box (recipient phone →
  Send test → green/red result).
- **Invoice editor, finalized invoice, header action row**
  (`web/src/pages/invoices/InvoiceEditorPage.tsx`): **"Send on WhatsApp"**
  button next to Download PDF, disabled until `pdf_status === "rendered"`.
  Opens `WhatsappSendDialog`:
  - checkbox **"Send to `<party>` (`<phone>`)"** — shown **only if the party
    has a phone**, pre-checked
  - **"Also send to another number"** field — always blank
  - sends to either or both (one endpoint call per target), per-target result
    list
  - no party phone → no checkbox; must type a number; Send disabled until a
    ≥10-digit number is entered
  - a bare 10-digit number is treated as India (+91)

---

## 6. Templates (Meta-side, WABA `1082044344274334`)

### `invoice_ready` — APPROVED (id `937644802113180`), `en`, UTILITY

| Component | Value |
|---|---|
| HEADER | **DOCUMENT** (PDF sample uploaded) |
| BODY | `Hi {{1}}, your invoice {{2}} is ready. Amount: ₹{{3}}. The PDF is attached.` |
| FOOTER | `Fleek Fintech` |

`{{1}}` = party (customer) name, `{{2}}` = invoice number, `{{3}}` = amount.
`{{3}}` is a **Number** variable → the code sends bare `f"{grand_total:.2f}"`
(no `₹`, no thousands separators; the `₹` is in the template's static text).
`{{1}}`/`{{2}}` are **Name** variables (the Utility editor only offers
Name/Number; both accept plain text on send).

**Gotcha — the first approval was wrong.** The template was first created with
the hint text `"Media -> Document"` typed into the **Header text box**, so it
approved as `format: TEXT`. Sends then failed with
`132012 "header: Format mismatch, expected TEXT, received DOCUMENT"`. It was
edited to a real **Media → Document** header (with a sample PDF), which sent
it back to **PENDING**, then approved. If a send 132012s with that message
again, Meta is still serving a stale TEXT version — wait for the edit to
clear.

### `payment_reminder`

`TEMPLATE_BODY_PARAMS` has an entry (same 3 params) but the template is **not
confirmed created/approved in Meta**. Verify before relying on it.

`TEMPLATE_BODY_PARAMS` in `services/whatsapp.py` is a **global dict**, not
per-tenant — fine while all firms share one WABA; a firm on its own WABA would
need identically-named/shaped templates or its sends 132001.

---

## 7. Meta number onboarding — runbook

### First-number saga (recorded — the `133010` loop)

`+91 89565 06362` took an extended debugging session. Every send returned
**`133010 "Account does not exist in Cloud API"`** and `/register` returned a
misleading **`100 "Unsupported request - method type: post"`**, even though
`platform_type: CLOUD_API`, `code_verification_status: VERIFIED`, token
scopes correct, template approved.

**Root cause:** the FleekWA **System User had the phone number assigned as an
asset but not its parent WABA `1082044344274334`.** Every WABA-level
operation (register, ToS, templates) silently failed because the token
couldn't see the WABA. The **old** token in use had this gap.

**Fix:** generate a **fresh** System User token (still same FleekWA app, same
System User) **and** assign WABA `1082044344274334` to that System User
(Business Settings → System Users → Add Assets → WhatsApp accounts → Full
control). Then:

```
POST /v25.0/{pnid}/request_code   {"code_method":"SMS","language":"en"}   → {"success":true}, SMS OTP to the SIM
POST /v25.0/{pnid}/verify_code    {"code":"<OTP>"}                        → {"success":true}
POST /v25.0/{pnid}/register       {"messaging_product":"whatsapp","pin":"250181"}  → {"success":true}
```

The app also had to be **published** (was in Development). Business
Verification + a payment method were already done.

**PIN `250181`** is now that number's two-step verification PIN — needed for
any future re-registration. Deregister: `POST /{pnid}/deregister` (10 calls
per number per 72h).

### For each NEW firm

1. Operator adds the firm's WhatsApp Business number to the **FleekWA WABA
   `1082044344274334`** in Meta (partner-share the firm's existing WABA into
   FleekWA's Business Manager, or create the number under FleekWA).
2. Register it: `request_code` → `verify_code` → `register` (needs SIM
   access for the SMS OTP; choose a 6-digit PIN).
3. `PUT /api/admin/firms/{tenant_id}/whatsapp` with the new
   `phone_number_id` + `waba_id` (or via the Ops console panel).
4. **Skip** template creation — the shared WABA already has `invoice_ready`.
5. App publish / Business Verification are **not** repeated.

---

## 8. Testing / verification

- `POST /api/admin/firms/{id}/whatsapp/test` with `{to_phone: "<your number>"}`
  → `invoice_ready` + a throwaway PDF lands on the phone, response
  `{"status":"sent","wa_message_id":"wamid..."}`. **This path is verified.**
- The full invoice path (editor button → real WeasyPrint PDF →
  `POST /api/invoices/{id}/whatsapp`) is **not yet tested live**.
- Full backend suite after all commits: **364 passed, 1 skipped**.
- WeasyPrint needs native libs (absent on a bare Windows dev box); the
  test-send PDF is hand-built bytes to sidestep it, and `render_invoice_pdf`
  imports weasyprint locally so the API still boots without it.

---

## 9. PENDING / open items

1. **`WHATSAPP_APP_SECRET` for metalerp-api** — add to
   `fleek-infra/scripts/load-vault-secrets.sh`
   (`_lvs_export METALERP_WHATSAPP_APP_SECRET fleek-backend/core whatsapp_app_secret`)
   and `docker-compose.yml` (`WHATSAPP_APP_SECRET: ${METALERP_WHATSAPP_APP_SECRET}`).
   Without it the inbound status webhook 503s in prod (delivery/read receipts
   never land; **outbound sends unaffected**). Infra change → push
   `fleek-infra` first, then Metal-ERP.
2. **`payment_reminder` template** — create + get approved in Meta, or drop
   the `TEMPLATE_BODY_PARAMS` entry.
3. **End-to-end app test** of `POST /api/invoices/{id}/whatsapp` from the
   editor button, on a real finalized invoice.
4. **`WhatsappSendDialog`** — committed but **not visually reviewed** (project
   guardrail: visual review before UI ships).
5. **`invoice_ready` FOOTER** `Fleek Fintech` — Meta-side edit if unwanted.
6. **Second firm** — no real second firm/number onboarded yet; the
   "for each new firm" runbook above is untested past firm A.

### Not part of WhatsApp, raised in the same session

- **Invoice PDF signature block** — `for {{ tenant.trade_name or
  tenant.legal_name }}` removed from `invoice_v1_nongst.html`, leaving just
  `Authorised Signatory` (margins combined 18+24 → 42pt). **Uncommitted.**
  Deliberate per user request.
- **Invoice PDF header seller block** (`invoice_v1_nongst.html:36`, firm
  name + address + city + PAN + contact) — user flagged it, considered
  logo-vs-name, then said **"Hold — I need to think about this."**
  **Not touched.** Removing it would leave the invoice with no seller
  identification in the header — flagged as legally questionable.

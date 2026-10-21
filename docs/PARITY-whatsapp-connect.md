# Parity table: WhatsApp connect, advisor (Fleek) vs shop owner (Metal ERP)

Rule: Metal ERP copies Fleek. Every row names the Fleek source (read in full, 2026-10-21). A row marked **NO FLEEK COUNTERPART** needs the owner's
sign-off or is cut. Nothing in this table is built yet.

Fleek sources: page `fleek-frontend/src/advisor/views/WhatsAppIntegration.vue` (V), routes `fleek-backend/routes/whatsapp_integrations.py` (R),
service `services/whatsapp_embedded_signup.py` (S), `services/org_whatsapp_service.py` (O), `services/crm_plan_service.py` (P), `services/wa_profile.py` (W).

## A. The page

| Element | Fleek (source) | Metal ERP |
|---|---|---|
| App id, config id | Public build vars `VITE_WHATSAPP_APP_ID`, `VITE_WHATSAPP_CONFIG_ID` (V:74-75; values set in infra `fleek-frontend/Dockerfile`) | Same two build vars on the SPA build (3 places: Dockerfile, compose arg, CI). **No backend route.** |
| Facebook SDK | Loaded on mount, `FB.init({appId, cookie:true, xfbml:false, version:'v25.0'})`, 10 s timeout (V:140-156,187-199) | Same |
| Popup | `FB.login(sync callback, {config_id, response_type:'code', override_default_response_type:true, extras:{setup:{}, featureType: business_app ? 'whatsapp_business_app_onboarding' : '', sessionInfoVersion:'3', version:'v4'}})` (V:257-301) | Same |
| Finish message | origin `endsWith('facebook.com')`; events FINISH, FINISH_ONLY_WABA, FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING, FINISH_OBO_MIGRATION, FINISH_GRANT_ONLY_API_ACCESS (V:163-185) | Same |
| Number-type choice | Dialog "Which number are you connecting?" business-app vs new number, warning, "what changes" list (V:415-440) | Same text |
| Business-app explainer | Dialog "Connect with WhatsApp", 3 steps, Open WhatsApp (V:442-457) | Same text |
| PIN dialog | "Set a WhatsApp PIN", 6 digits, asked BEFORE the popup (V:459-, 227-247) | Same |
| Pre-flight list | "Before you press Connect": number, Facebook login, business name, payment, daily limit, PIN (V:326-338) | Same text, "Fleek" replaced by who the owner should contact |
| Status line | not connected / needs reconnect / registration pending / connected + number / business name / "Also works on your phone" (V:341-364) | Same |
| Retry registration | PIN field (optional) + Retry button (V:403-413, 111-136) | Same |
| Disconnect | confirm dialog, then disconnect (V:304-314) | Same |
| Profile card + Sync now | Logo/about/website/address/email read from the org's `crm_settings` (W:156-169) (V:365-396) | **Does not apply to a shop without the CRM core:** there is no `crm_settings` row. Decision 1 below. |
| "What happens next" | Reads `/api/crm/setup` (CRM setup steps) (V:398-402) | **Does not apply** without the CRM core. |
| "Ask Fleek" link | support email from `/api/config/public` (V:60-72) | Same, from Fleek's public config via the one signed channel, or omitted. Decision 2. |
| Org-shared connection ("connected by a colleague") | crm_only orgs share one number across users (O:42-55, V:353) | **Does not apply:** a shop has one owner row (decision 3). |

## B. The routes (advisor session in Fleek, signed call in Metal ERP)

| Fleek route | What it does (source) | Metal ERP route |
|---|---|---|
| `GET /api/advisor/whatsapp-integrations` | status: connected, needs_reconnect, needs_registration, display number, business name, connection_mode, profile (R:35-82) | `GET /api/whatsapp-integrations`: pass-through. Response = Fleek's. |
| `POST .../connect` | `connect_number`: plan cap check, exchange code, fetch display number + business id, register (cloud only, PIN encrypted), upsert, audit, subscribe app to WABA, copy templates, copy Flows, push profile (R:90-250) | `POST /api/whatsapp-integrations/connect`: pass-through of the same body. Fleek runs the same function. |
| `POST .../retry-registration` | optional PIN; coexistence refuses (R:253-310) | pass-through |
| `POST .../disconnect` | sets `revoked_at`, audit (R:313-358) | pass-through |
| `POST .../sync-profile` | push org branding (R:236-248) | pass-through, only if decision 1 keeps the profile card |

Only the owner role may call them, as `org_admin` is the only Fleek role that can disconnect a colleague's number (O:58-64).

## C. The one structural difference (no Fleek counterpart, unavoidable)

An advisor's call carries a Fleek session. A Metal ERP owner has none (login stays in Metal ERP this phase). So Metal ERP's server calls Fleek's
function **signed, with the shop's Fleek org id**, and Fleek resolves that org's owner row, then runs the existing function. This is the signed
channel that already exists (entitlements, contacts). No other new Fleek-side surface is added.

## D. What Metal ERP stores

| Item | Fleek | Metal ERP |
|---|---|---|
| The shop's number | one row, `advisor_whatsapp_connections` | A cache in `tenant_whatsapp_config` (the table predates this work). Needed because Metal ERP sends and matches status webhooks from its own server (`get_config`, `config_for_phone_number_id`) and cannot read Fleek's database. Filled from Fleek's status response after connect, retry and disconnect, and refreshed on the licence refresh. Never pushed by Fleek. |

## E. Gaps in Fleek's own rules that affect a shop (decisions)

1. **Profile card** needs `crm_settings`, which a shop has only if it holds the CRM core. Options: show it only for CRM-core shops, or omit it.
2. **"Ask Fleek"** link: use Fleek's support email, or omit.
3. **Number cap:** `whatsapp_connect_block_reason` (P:412-433) only limits `crm_only` orgs; for a Metal ERP org it returns nothing, so the plan's `whatsapp_numbers: 1` would not be enforced. Fleek's own rule would need `metal_erp` added. This is a change to Fleek code.

## F. Removed (invented, not in Fleek)

Signup-config route and the hard-coded config id; the admin Disconnect; the admin "tell Metal ERP again"; the Fleek-to-Metal ERP number push and its
pending state; the six-state mockup; the separate owner "connect" mockup copy.

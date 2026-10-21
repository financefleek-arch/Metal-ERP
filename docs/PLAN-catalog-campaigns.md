# Catalog campaigns (WhatsApp carousel to a shop's own past customers)

Started 2026-10-20. Related: `docs/PLAN-customer-ordering.md`, `docs/PLAN-price-list-on-request.md`,
memory rules `whatsapp-promotional-cost-rule` and `catalog-send-consent-storage-2026-10`.

Status: PLAN ONLY. Nothing built. Items marked VERIFY are Meta facts from memory, not yet checked
against current docs.

## 1. What we are building

A shop sends a "new range" message to customers it has **sold to before**: a body text plus a swipeable
carousel of category cards, each with a "View catalogue" button opening that customer's own `/c/<token>`
page. The customer orders there; the existing order flow creates the draft invoice. Reference look: the
Vishnu Enterprises "Ready for your 2027 range?" message (screenshot 2026-10-20).

This is a Meta **media carousel Marketing template**. No WhatsApp Flow is needed for v1.

Audience is fixed: own past customers only (decided 2026-10-20). No imported or purchased lists.

## 2. Decisions already made

- Fleek is a Tech Provider; each shop onboards through **Embedded Signup with coexistence** (shop keeps its
  WhatsApp Business app and the API on one number).
- Each shop therefore gets **its own WABA**: own templates, own quality rating and limits, own billing.
- Audience = past customers only.

## 3. The consent question (read first)

"We sold to them" is a relationship, not an opt-in. Meta's policy asks that the person agreed to receive
messages from the business **on WhatsApp**, and the agreement can be collected anywhere (counter, invoice,
web) but must be clear about what they will get. Low risk is not no risk: a high block rate lowers the
shop's own messaging limit, and with its own WABA the damage stays with that shop.

Proposal, uses the parked `0054` backend (`party.wa_catalog_consent*`, `services/catalog_consent.py`):

1. **Counter tick.** At billing, a "Customer agreed to offers on WhatsApp" toggle (counter staff may record,
   source `manual`). This is the cleanest evidence.
2. **Order page.** Checkbox "Send me new ranges and offers on WhatsApp", default ticked (decision from the
   consent thread 2026-10-08), unticking = opt-out.
3. **Customer replies.** Any customer who has already messaged the shop can be asked "Reply YES for new
   ranges"; a STOP reply sticks and is never overwritten by a weaker signal (already how `0054` works).
4. **Backlog of old customers.** Campaign audience screen shows "N consented, M not asked". Not-asked
   customers are NOT sent to. Shop works them down through 1-3. This is the real cost of the feature and I
   would not bypass it.

Open: the user previously said "we will solve consent later". This plan makes it a prerequisite for
campaigns. Confirm that is acceptable.

## 4. Template (per shop WABA)

One template per shop at first, `catalog_range_carousel`, category Marketing, language English (Hindi later).

| Part | Content |
|---|---|
| Body | `{{1}}` headline, `{{2}}` product count line, `{{3}}` shop phone(s). Greeting by customer name if it fits wording. |
| Cards | 2-10, image header (same aspect on all), card text, buttons |
| Card button 1 | URL, dynamic suffix `{{1}}` = recipient's catalog token: `https://<host>/c/{{1}}` |
| Card button 2 (optional) | URL to the shop's WhatsApp community invite, if it has one |

Why this works for us: Meta approves structure and a sample once. Images, texts, body variables and the URL
suffix change on every send without re-approval, so each campaign is data, not a new template.

Constraints (VERIFY): all cards share one header format and one button layout; card count and text limits;
dynamic URL suffix only at the end of the URL; image aspect ratio rules; marketing templates may be
reclassified or paused for quality.

Templates belong to a WABA, so Fleek **creates them through the template API on each shop's WABA at
onboarding** (including the sample image handle via resumable upload), then tracks approval status per firm.

## 5. Campaign flow (shop side)

1. **New campaign**: pick categories (cards auto-built: best photo of the department, name, item count).
   Edit headline and body line. Live phone-style preview.
2. **Audience**: consented past customers. Filters: bought in last N months, has outstanding balance
   (exclude), city. Show count and consented / not-asked split.
3. **Cost line**: `recipients x rate = Rs X` (VERIFY India marketing rate, about Rs 0.86 per message when last
   noted), against the shop's monthly cap. Cannot send over the cap.
4. **Send now or schedule.** Sending is queued and throttled; failures recorded per recipient.
5. **Results**: sent, delivered, read, link opens, orders, order value, blocks and STOPs. Orders attribute by
   the per-recipient token (see section 6).

Guardrails: per-recipient frequency cap (Meta's own per-user marketing cap also applies, VERIFY), quiet
hours, skip recipients who ordered or were messaged in the last N days, auto-pause the shop's campaigns if
the WABA quality rating drops.

## 6. Personal links (this is O5 from the ordering plan)

Each recipient gets their own share-link token, so a click identifies the customer and a campaign id
without asking for name and phone. That requires: a link-per-party model (today a link is anyone-with-link),
prefilled name and phone on the order, and a `campaign_id` carried on the token for attribution. Per-customer
prices (rest of O5) can follow and are not needed for v1.

## 7. Data model (sketch; check section 8 first, much may be reusable)

- `tenant_whatsapp_config`: add `waba_id`, per-firm token reference, `onboarding_mode` (`manual` | `embedded`),
  `coexistence` flag, quality rating and messaging limit (from webhook).
- `wa_template`: tenant, name, language, category, status, meta template id, rejection reason, components JSON.
- `wa_campaign`: tenant, template, categories JSON, audience filter JSON, status, scheduled_at, cost estimate,
  counts.
- `wa_campaign_recipient`: campaign, party, link token, status, whatsapp message id, error, ordered flag.
- Order gets `campaign_id` (nullable).
- Cap: `tenant.campaign_monthly_cap` (messages), shown beside the cost line.

## 8. Embedded Signup + coexistence: ALREADY BUILT in fleek-backend (reuse, do not rebuild)

> Where a shop connects, who owns templates and licences: decided 2026-10-20, see `docs/PLAN-fleek-control-plane.md`.
> Fleek is the control plane, Metal ERP stays the product, login moves later. The (a)/(b) question below is superseded:
> use an org-level connection owned by the Fleek side (control-plane plan P3), connected Fleek-assisted at first.

Checked 2026-10-20 in `fleek-backend` code, `Plan/features/B23`, `B53`, `B52q`, `B52y` and memory. The earlier
version of this section proposed building this from scratch; that was wrong.

What exists there (for advisor orgs, keyed on `advisor_whatsapp_connections`):
- **Embedded Signup** (B23): `services/whatsapp_embedded_signup.py` (code exchange), `routes/whatsapp_integrations.py`
  (`connect`, `status`, `disconnect`), JS-SDK popup in `WhatsAppIntegration.vue`. Tech Provider model: Fleek's one System
  User token already covers every shared WABA, so **no per-shop token is stored** (same as `get_config` here).
- **Coexistence** (B53): connect choice, `connection_mode`, all five Meta finish events, raw capture of
  `smb_message_echoes`, `history`, `smb_app_state_sync`, `account_update`. Verified working in India with a test number.
  P2-P4 (echoes into inbox, history import, lifecycle) not built.
- **Per-WABA webhook subscription** (`subscribe_app_to_waba`). Without it status stays frozen; found as a real prod bug.
- **Template machinery**: `whatsapp_template_definitions` + `whatsapp_template_service.set_template` creates and pushes
  to every connected WABA and to Fleek's WABA; `whatsapp_template_sync` adopts a template already approved in Meta;
  header IMAGE/VIDEO, footer, up to 3 buttons, per-send dynamic URL; rollout status per WABA fed by webhook.
- **Limits, cost, quality**: `crm_meta_limit` (portfolio tiers 250/2k/10k/100k), `crm_wa_usage` (counts by Meta category,
  link to WhatsApp Manager Insights), `crm_wa_quality`, `crm_wa_errors` (131049 etc.).
- **Consent and opt-out**: `crm_consent`, STOP and phrase detection, Meta `user_preferences` stop/resume,
  `wants_out` inbox state, wrong-number handling, per-contact frequency guard, attempt cap and "Undecided" state (B52y).
- **Locked owner rule (B52o/reference_crm_infra):** Meta bills each org directly; Fleek shows counts and a link to Meta
  Insights, no rupee estimate. Also: "never build a customer-typed template-name/button mapping or per-customer editor".
- **Fan-out to Metal ERP** already exists (`routes/intelligence.py::_fanout_whatsapp_webhook`): raw body + signature
  forwarded to `METALERP_WHATSAPP_WEBHOOK_URL`.

What does NOT exist (the real gaps):
1. **No carousel template support** anywhere (grep for "carousel" empty in fleek-backend). `_build_template_components`
   knows header/body/footer/buttons only.
2. **Connections belong to advisors, not Metal ERP shops.** `connect()` requires an advisor session and writes
   `advisor_whatsapp_connections`. A Metal ERP tenant has no row there; Metal ERP reads `tenant_whatsapp_config`,
   filled by hand in the Ops console.
3. The webhook fan-out is a blind forward of everything; Metal ERP can't tell coexistence events from status events
   and the coexistence capture only attributes events to advisor connections.
4. Metal ERP has no template machinery of its own: it sends four fixed templates by name.

Decision needed before building (do not pick silently): **where does a Metal ERP shop connect its number?**
- **(a) Shared connect service in fleek-backend**, offered to Metal ERP tenants. New connection table or a generic
  owner type (advisor | metal_erp_tenant) so `connect`, subscribe, template push and coexistence capture are reused,
  and Metal ERP's `tenant_whatsapp_config` is written through an API. Least duplication; touches the advisor code path,
  which carries a lot of hard-won fixes and unrun DB tests.
- **(b) Metal ERP runs its own popup and connect endpoint**, calling Meta directly (same BISU code exchange, same
  subscribe call, same System User token). Copies about 300 lines of proven logic into a second codebase but leaves
  the CRM untouched.
- Either way: reuse the proven sequence (code exchange, `subscribe_app_to_waba`, display-number and business-id
  lookups, finish events, coexistence skips PIN and register) rather than rediscover it. Template create/push per
  WABA is a plain Graph call (`POST /{waba_id}/message_templates`) that both sides already know how to build.

Carousel: add a carousel branch next to the existing component builders, wherever templates are defined for Metal ERP.
Reuse the `whatsapp_template_definitions` shape (components JSON, source, status) rather than inventing a table.

Reused as-is from fleek-backend knowledge, not reimplemented: messaging-limit reading, 131049/131047 classification,
the STOP phrase list, Meta `user_preferences` handling, the cost-as-counts rule.

## 9. Out of scope: Meta ads

Decided 2026-10-20: promotion is WhatsApp only. No Click-to-WhatsApp ads, no Facebook or Instagram ad
creation, no ad-account integration, no audience upload. Growth comes from the shop's own past customers.
Reach beyond them is by the customers themselves: the catalog page's WhatsApp share button, and the shop
sharing its link or community group. Revisit only if the shop asks for new-buyer acquisition.

## 10. Build order

1. **Decide (a) or (b) in section 8**, then a **spike (1-2 days):** carousel template create + approval via API on
   a connected test WABA, one real carousel send to the pilot shop's own number. Confirms the VERIFY items in section 4.
   Embedded Signup and coexistence themselves are already proven in fleek-backend.
2. Per-firm connection for Metal ERP shops (per the decision), template status webhooks.
3. Consent capture UI (counter toggle, order page checkbox, STOP handling) on top of `0054`.
4. Personal links (O5 subset) + attribution.
5. Campaign builder, audience, cost line, queue, results.
6. Quality-rating monitoring and auto-pause.

## 11. Risks

- Consent debt: most past customers will be "not asked" on day one, so first campaigns will be small.
- Template rejection or reclassification, and per-WABA quality limits.
- Coexistence limits (throughput, feature gaps) and Tech Provider review timelines are outside our control.
- Cost surprises if caps are not enforced; the cost line and the cap are part of v1, not later.
- Language: Hindi and Bengali templates are separate approvals (open from the consent thread).

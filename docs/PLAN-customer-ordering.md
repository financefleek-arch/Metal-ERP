# Plan: customer ordering (catalog link, cart, order, invoice)

Status: **O0 to O4 BUILT 2026-10-06** (share links, public catalog page, cart, order placing, order numbers, status page, shop inbox with review and draft invoice, WhatsApp notifications; migrations 0049, 0050, 0051). Not built: O5 (per-customer prices, personal links, installable app). Written 2026-10-06. Next direction after the items and
supplier-catalog arc (see `PLAN-erp-foundation-masters-documents-media.md`, Closeout).

## 1. What the user asked for

A customer receives a **unique URL** to the shop's catalog, sees the items (photos, prices),
**selects items and places an order**, and the software **creates an invoice** from it.
Later this may be upgraded to a **customer app**. "Billing" here means sales invoices to
customers, not supplier bills.

## 2. The flow in one picture

```
shop makes a catalog link  ->  sends it (WhatsApp / any chat)  ->  customer opens it on a phone
  -> browses (groups, photos, prices)  -> adds quantities  -> identifies themselves  -> places order
  -> shop is told  -> shop reviews (edit / accept / reject)  -> one tap: draft invoice for that party
  -> shop finalizes the invoice as today (gap-free numbering, PDF, WhatsApp send, Tally push)
  -> customer sees status
```

The invoice is **not** finalized automatically. Finalizing is the heavy, numbered, irreversible
step (see `invoice-finalized-immutable`); an order creates a **draft** that the shop confirms.

## 3. What already exists and is reused

| Need | Already built |
|---|---|
| What the customer sees | Items with photos (signed URLs), availability (In stock default), selling price (`default_rate`), pack and carton size, groups, the customer catalog selection and versions (`/api/customer-catalogs`) |
| The customer | `party` (role customer), phone normalisation (`+91`), duplicate rules (`resolve_party`: GSTIN, phone, exact, fuzzy) |
| The invoice | Draft invoice CRUD with party optional, lines, totals, finalize, PDF, WhatsApp send (`invoice_ready` template, delivery tracking), Push to Tally |
| Safe public images | Hour-stable signed media URLs |
| Bulk and audit | Audit entries, role gate (`owner` / `accountant` write), background jobs |

## 4. What is new

1. **Share link** (public, no login): an unguessable token per link, pointing at a catalog
   (selection) or at "my in-stock items". Revoke, expiry, view count.
2. **Public catalog page**: a mobile-first web page served at a short URL. Only what a customer may
   see: name, photo, code, selling price, pack size, availability. **Never** cost price, supplier,
   margin, other customers, or stock figures.
3. **Cart and order**: quantities in sale units (a pack), optional note, delivery details,
   identification of the customer, and a submit that creates an **order**.
4. **Order data**: `customer_order` (party or unmatched contact, status, note, totals snapshot,
   source link) and `customer_order_line` (item, quantity, rate at the time of ordering).
5. **Shop side inbox**: new orders list with a badge, order detail with edit (quantities, remove,
   add), accept or reject with a reason, and **Create invoice** (draft for the matched party, lines
   from the order, rates from the order).
6. **Notifications (decided 2026-10-06: the customer has no app, so WhatsApp is the channel)**: the
   customer gets WhatsApp utility messages (order received, accepted, invoiced, rejected) plus a
   link to a plain web status page; the shop is told of a new order on WhatsApp too, and the shop's
   web inbox (the ERP, which staff do use) remains the place to work on it.
7. Later: per-customer links and prices, reorder, installable app (PWA first, then native if
   wanted).

## 5. Decisions (defaults used until you say otherwise)

| # | Question | Default used |
|---|---|---|
| D1 | One generic link per catalog, or one personal link per customer? | **Both, in this order**: generic link first (customer types name and phone), personal link (knows the party, no typing) in phase O4. |
| D2 | How does the customer identify themselves? | Name + phone on the order, no password. We **match** the phone to an existing party, or hold it as a new contact for the shop to confirm. Optional later: a one-time code by WhatsApp to prove the number. |
| D3 | Prices shown | The item's one selling rate (the same price printed in customer catalogs). Per-customer or tiered prices are a later phase; the order stores the rate at the time. |
| D4 | Does an order become an invoice by itself? | **No.** Order -> shop reviews -> one tap creates a **draft** invoice. Finalize stays manual. A later opt-in could auto-create the draft for trusted customers. |
| D5 | What can be ordered? | In stock items; expected items only if the link allows ("on order"); out of stock and discontinued are hidden. A quantity is never promised against stock (Tally stays the truth); the shop confirms on review. |
| D6 | Payment | Out of scope. Orders are "pay as per your terms"; payments stay in the existing payments module. |
| D7 | Minimum order, delivery charge, terms text | **Decided 2026-10-06: ONE setting for the whole shop** (minimum order value + one terms line, on the Firm page), not per link. No shipping engine. |
| D8 | Quantity unit | **Decided: whole packs only** (the item's pack, as priced). Piece-wise selling is a later option. |
| D9 | Order number | **Decided: `ORD-0042` style**, own counter per shop, separate from invoice numbers. |
| D10 | GST on the customer page | **Decided: prices shown before GST with the terms line** until GST billing exists. |
| D11 | Link token storage | Stored as issued (not hashed): the shop must be able to copy a link again, and a link is a capability URL like the signed photo URLs. Long random (96 bits), revocable, expirable. |

## 6. Security and abuse (non-negotiable)

- Token is long and random, stored hashed; links can be revoked and can expire.
- Rate limits on the public page and on order submit; a size cap on an order (lines and quantity);
  basic bot protection (honeypot field, per-IP and per-phone limits).
- The public API is a separate, read-only, tenant-scoped surface: no internal ids, prices only
  from the stored selling rate, no cost fields in any response (tested).
- Orders from the public link are untrusted input: validated, never auto-finalized, never able to
  touch the shop's data beyond creating an order.
- Customer phone numbers are personal data: only used to contact about the order.

## 7. Phases

| Phase | What | Rough size |
|---|---|---|
| **O0** | Settle D1 to D7 with the user; sketch the customer page and the shop inbox for review (visual review before coding, per the UI guardrail) | 0.5 day |
| **O1** | Share links (create, copy, revoke, expiry) from a customer catalog or an item selection; public read-only catalog page with photos, groups, search, prices; no ordering yet; leak tests | 3 to 4 days |
| **O2** | Cart, identify, place order (`customer_order*`, migration), confirmation page, rate limits | 3 to 4 days |
| **O3** | Shop inbox, review and edit, accept or reject, **Create draft invoice** for the matched or new party, audit, role gate | 3 to 4 days |
| **O4** | Notifications (in-app badge, optional WhatsApp to the shop), customer status page, personal links per customer, repeat order | 3 days |
| **O5** | Per-customer prices, min order and terms, installable web app (PWA) | later |

Meta WhatsApp templates (for example `order_received`, `order_update`) are needed for any message
**we** start; they take days to approve. (The invoice, payment reminder and account statement templates are already approved; only the three order templates remain.)
Start that in O0.

## 8. Risks

| Risk | Handling |
|---|---|
| A public link leaks cost or supplier data | Separate public serializer, allow-list of fields, tests that assert the absence of cost fields |
| Spam or fake orders | Rate limits, honeypot, review-before-invoice, revoke link |
| Customer orders something not available | Availability filter, shop reviews every order, order line can be adjusted or dropped |
| Duplicate customers from typed names and phones | Reuse `resolve_party` (GSTIN, phone, exact, fuzzy) and show "same as an existing customer?" on review |
| Order becomes a heavy invoice problem | Order only ever creates a **draft**; finalize numbering is untouched |
| Scope creep into a full e-commerce app | D6 and D7 keep payment and shipping out; the app comes after the web flow proves itself |

## 9. Open questions for the user

1. ~~Generic or personal link~~ Settled 2026-10-06: **anyone with the link can order** (generic link first; personal links stay a later option, O4).
2. ~~Name + phone or known customers only~~ Settled 2026-10-06: **name + phone is enough**; an unknown number arrives as a new contact flagged "new customer, confirm" and nothing happens until the shop reviews it.
3. ~~WhatsApp or in-app~~ Settled: WhatsApp for the customer (no app exists) and for the shop alert; templates submitted in O0.

## 10. WhatsApp notes (from general knowledge of the Meta Cloud API, 2026-10-06; verify before relying on numbers)

- **Categories.** A message we start is a template in one of: utility, marketing, authentication. Order
  confirmations, order status updates and invoices tied to something the customer did are
  **utility**. Sending the catalog link on our own initiative is **marketing**. Anything promotional
  in a utility template gets re-categorised as marketing by Meta, so keep wording transactional.
- **24-hour window.** If the customer messaged the shop's number in the last 24 hours, free-form
  replies are allowed. A web order is not a WhatsApp message, so our "order received" and "order
  accepted" messages are business-started and need approved templates.
- **Cost (India, approximate).** Per-message pricing since mid-2025: utility is cheap and is free
  inside an open 24-hour window; marketing costs several times more. Check current rate cards.
- **Limits.** Messaging limits count unique customers contacted per rolling 24 hours (starting at a
  few hundred, rising to 1,000, 10,000, 100,000 with volume and quality). Limits apply at the
  business-portfolio level, which matters because all firms share one WABA today. Marketing also
  has per-customer frequency caps; utility does not.
- **Opt-in.** Business-started messages need the customer's opt-in: add a checkbox on the order page
  ("send order updates on WhatsApp").
- **Templates to submit (utility):** `order_received` (to the customer), `order_update` (accepted,
  invoiced, rejected), `new_order_alert` (to the shop's own number). Approval takes days; submit in O0.
- **Customer channel.** There is no customer app, so WhatsApp (templates above) is the only way to reach the customer after the order; the web status page is a link inside those messages.
- **Shop channel.** WhatsApp `new_order_alert` by default; email as a fallback; the ERP inbox is where the order is reviewed.

## 11. O1 build notes (2026-10-06)
- **Shop side:** Items selection bar "Share catalog link" -> dialog (name, show on-order items, optional expiry) -> link, Copy, Send on WhatsApp (a wa.me click-to-chat link, no API), Preview. Items > Catalogs lists the links (copy, stop, resume, views). Firm page: one shop-wide minimum order + terms line (`tenant.order_min_value`, `tenant.order_terms_line`).
- **API:** `/api/share-links` (create, list, patch: title, include_expected, expires_at, revoked; creating and stopping are audited), public `GET /api/public/catalog/{token}` (no login, 60 requests per minute per address, in-process limiter). Unknown, stopped and expired links all answer the same 404.
- **Public view = allow-list** (`PUBLIC_ITEM_FIELDS`): id, name, code, group, price, pack_qty, photo_url, thumb_url, tag. A test pins the exact keys and asserts no cost, supplier, margin, purchase rate or Tally text appears. Only in-stock items with a price; expected items only if the link allows ("On order").
- **Live, not a snapshot:** a link follows current prices and availability; a filter-based link re-evaluates its filter on each view.
- **Customer page:** `/c/<token>`, outside login and the app shell, mobile first: search, group chips, photo grid, price per pack, zoom, minimum order and terms line. No cart yet (O2).
- **Tokens** are 96-bit random, stored as issued (D11). Photos load through the existing signed media URLs.
- **Tests:** 12 in `tests/catalog/test_share_links.py`; full suite passes with foreign keys on. Browser-verified (shop dialog, link, logged-out phone view, bad link, panel, Firm card).
- **Known limits:** the rate limiter is per API process; the page loads the whole catalog at once (cap 3,000 items); photos on newly added items arrive after the background copy.

## 12. O2 build notes (2026-10-06)
- **Customer:** on `/c/<token>` add whole packs (stepper), review, give name + phone (+ optional firm, note), tick the WhatsApp opt-in, place the order. Cart and details are kept in the browser only. The confirmation shows `ORD-0042` and a status link `/o/<token>`; the status page refreshes itself.
- **Server decides everything:** price from the item (a forged rate is ignored), only items the link offers right now, whole packs 1 to 999, at most 100 lines, shop-wide minimum order, a double tap returns the same order (2-minute window on phone + basket), items that went out of stock answer 409 with their names (the page removes them and says so).
- **Abuse limits:** hidden honeypot field, 10 orders per hour per address and 5 per hour per phone (in-process), 60 status views a minute.
- **Numbering:** `ORD-0001...` from a counter per shop (`code_sequence`, prefix ORD, row-locked), separate from invoice numbers.
- **Customer match:** the phone is matched to an existing party and only suggested; the shop confirms on review.
- **FK safety:** deleting an item, a customer or a supplier no longer breaks on orders, price history or defaults (references cleared; order lines keep their name and rate).

## 13. Meta template sample values (for the submission form)
Meta asks for an example for every variable. Use realistic values; no ₹ sign or thousands comma in amount variables.
- `order_received`: "Hi {{1}}, {{2}} has received your order {{3}} ({{4}} items, ₹{{5}}). We will confirm availability shortly." Samples: 1 = Ramesh, 2 = Kumar Steel House, 3 = ORD-0042, 4 = 2, 5 = 3082.00. Button: Visit website, Dynamic, `https://<site>/o/{{1}}`, sample `Ab12Cd34Ef56`.
- `order_update`: "Hi {{1}}, your order {{2}} is {{3}}. {{4}}" Samples: 1 = Ramesh, 2 = ORD-0042, 3 = confirmed, 4 = Invoice 1224 for ₹3082.00 is attached. Same button.
- `new_order_alert`: "New order {{1}} from {{2}}: {{3}} items, ₹{{4}}. Open Metal ERP to review it." Samples: 1 = ORD-0042, 2 = Ramesh Gupta, 3 = 2, 4 = 3082.00. No button.
- Category Utility; no promotional words. In WhatsApp Manager the URL button is "Add button > Visit website".

## 14. O3 and O4 build notes (2026-10-06)
**O3: the shop reviews the order and makes a draft invoice**
- Orders tab (New badge). Order detail: who the customer is (phone match suggested; "Add as a new customer" or pick an existing one; a second "new customer" with the same phone or name is refused), the lines with quantity boxes and remove, hints for what changed since the order ("not in stock now", "price now ₹..."), the customer's note, and the WhatsApp messages sent.
- Edit lines (`PUT /api/orders/{id}/lines`): set packs per item, 0 removes, any priced item can be added at its current price; a line that stays keeps the rate the customer agreed to; an order cannot be emptied (reject it instead). Accept (`new -> accepted`), Reject (a reason is required and is shown to the customer).
- **Create draft invoice** (`POST /api/orders/{id}/invoice`): needs a confirmed customer; makes a DRAFT sales invoice (lines = whole packs at the pack rate, description "(pack of N)", item and HSN carried over, notes "Order ORD-0042. <customer note>"). Never numbered or finalized here. One draft per order.
- Status flow: new -> accepted -> **invoiced when the invoice is finalized** (hook in the finalize endpoint); deleting the draft reopens the order (back to accepted, invoice link cleared). Every action is audited and needs a writer role.
**O4: WhatsApp**
- Events: placed (customer "order received" if they ticked the box, and the shop alert if an alert number is set), accepted, rejected (with the reason), invoiced (with invoice number and amount). Sent after the request is answered, in a background task with its own session; never breaks the order; each (order, event, template) is sent once; failures are kept on the order with Meta's reason. A firm with no WhatsApp setup simply skips.
- Templates and button: `order_received` and `order_update` end in a dynamic URL button ("View order", variable = the status token, sent as a button component); `new_order_alert` has none. Parameter order is in `TEMPLATE_BODY_PARAMS` (`services/whatsapp.py`), sample values in section 13.
- Shop alert number: Firm page > Customer catalog page > "Tell me on WhatsApp when an order arrives" (`tenant.order_alert_phone`), separate from the sending number, which cannot message itself.
- Tests: 21 order tests (`test_customer_orders.py`) and 9 notification tests (`test_order_notifications.py`, Meta stubbed); full suite passes with foreign keys on. Browser-verified: placing, review, edit, new customer, draft invoice.
- **Not verified against real Meta:** the templates must be approved first (submit them with the section 13 samples), and the first real send should be checked in `docker logs` and the order's WhatsApp list.

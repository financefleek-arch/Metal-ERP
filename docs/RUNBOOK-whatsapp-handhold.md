# Runbook: hand-holding a Growth shop through WhatsApp setup

For the Fleek team. WhatsApp from the shop's own number is part of Growth (999) and Complete (1,499), and a shop cannot do it alone the first time, so
every Growth shop gets a guided session, **inside its 3-day free trial**, so book it the moment the trial starts (not after the first charge). Written from `docs/PARITY-whatsapp-connect.md`, `docs/GO-LIVE-control-plane.md` (section C),
`docs/PLAN-catalog-campaigns.md` and `docs/FEATURE-whatsapp.md`. Items marked **(verify)** are not confirmed by anything in the repo; check them on the first
real session and correct this page.

## 1. How it works (so you can explain it in one breath)
- Fleek is a Meta **Tech Provider**. The owner connects from the Metal ERP **Firm** page through Meta's Embedded Signup popup, **with coexistence**:
  the shop keeps using the WhatsApp Business **app** on the phone and our API uses the same number.
- Each shop gets **its own WhatsApp Business Account**: own templates, own quality rating, own daily limit, and **Meta bills the shop directly** for message charges.
- On connect, Fleek copies the six shop templates to the shop's account (`invoice_ready`, `payment_reminder`, `account_statement`, `order_received`,
  `order_update`, `new_order_alert`). They are already approved on Fleek's own account; a fresh copy on the shop's account can take time to approve **(verify)**.
- Only the **owner** can connect or disconnect. A counter user cannot.

## 2. Before the session (CS does this, 5 minutes)
Send the owner this on WhatsApp the moment the trial starts (the same list the Setup card shows, "ready" items):
1. The number must be on the **WhatsApp Business app** (not regular WhatsApp), version **2.24.17 or later**, and have been **in use for about a week**.
2. It must **not** be on regular WhatsApp. This is the commonest failure (Meta error 2494064). If it is, the owner must move to the Business app first (a separate step; do not start Connect).
3. The owner is a **Facebook admin of their business** (Meta Business account) and knows the login. If the business account is held by a nephew or an accountant, that person must be on the call.
4. A **card or UPI ready for Meta's own message charges** (separate from our 999). Tell them the order of magnitude **(verify current Meta India rates before quoting)**.
5. A **6-digit PIN** they choose for WhatsApp registration. They must remember it; we never see it.
6. Business name as it should show to customers.
If any of 1 to 3 is not true, **do not book the call**; fix that first. A failed popup wastes the owner's goodwill.

## 3. The session (about 20 minutes, screen share or phone beside them)
| Step | Owner does | You check |
|---|---|---|
| 1 | Open Metal ERP, Firm page, WhatsApp section, press Connect | Plan shows Growth or Complete (the section is hidden on Free) |
| 2 | Choose "business app number" in the dialog; read the three "what changes" lines aloud with them | They understand the phone app keeps working |
| 3 | Type the 6-digit PIN | Written down by them, not by you |
| 4 | Facebook popup: log in, pick/confirm the business, accept | Popup not blocked by the browser; pop-ups allowed |
| 5 | Popup closes; page shows **Connected** with the number and business name | Status line, "Also works on your phone" |
| 6 | Send a test: any finalised invoice, Send on WhatsApp, to **their own phone** | Delivered, read ticks; the PDF opens |
| 7 | Add the payment method in Meta's WhatsApp Manager if Meta has not asked yet | Meta will not deliver business-initiated messages without it **(verify exact trigger)** |
| 8 | Show Collections and Orders; turn reminders on (they are proposals, never auto-sent) | They know they approve before anything goes out |
Done = status Connected **and** one test message delivered **and** the owner has seen where reminders are approved.

## 4. When it goes wrong
| Sign | Cause | Do |
|---|---|---|
| Error 2494064 | number is on regular WhatsApp | Stop. Owner moves to the Business app, waits for it to be stable, we rebook |
| Status "needs reconnect" | token or link lapsed | Owner presses Connect again; no data lost |
| Status "registration pending" | registration did not finish | Retry registration on the page, with the PIN; if refused on a coexistence number, reconnect instead (Fleek refuses retry for coexistence) |
| Popup does not open or closes at once | browser pop-up blocker, or the Facebook login is not a business admin | Allow pop-ups; confirm the Facebook account is the business admin |
| Connected but test message fails | Meta payment method missing, or template not yet approved on the shop's account | Check WhatsApp Manager billing; check template status in the Fleek WhatsApp Templates console |
| "Not on your plan" on the page | Shop is on Free or Starter | Plan problem, not setup: see Plan and billing |
| Plan allows one number and they want two | `whatsapp_numbers` limit is 1 | Plan question; Fleek's own number-cap check does not yet cover Metal ERP orgs, so enforce by hand **(see PARITY section E)** |
Escalate to engineering only for: signed-call errors from Metal ERP to Fleek, or a Connected shop whose status webhooks never arrive.

## 5. Consent, before they message customers about offers
Order and invoice messages are service messages. **Offers and catalogs are Marketing** and need the customer's WhatsApp consent
(`docs/PLAN-catalog-campaigns.md` section 3): counter tick at billing, order-page checkbox, a YES reply. Tell the owner now, once: do not send offers to customers who
never agreed; it lowers the shop's own sending limit. (That feature is parked; the sentence is not.)

## 6. After the session (email nudges for now)
- Day 0: email "You are connected. Here is what to try this week" (3 lines, no jargon).
- Trial day 2: if status is not Connected, the shop is on the **call list** (Fleek admin Home, "WhatsApp setup pending"). If the delay is on Meta's side or ours, extend the trial (up to 4 days, pending the owner's OK) so they are not charged before they have seen it work.
- Day 7: if connected but no invoice has been sent on WhatsApp, email: "Send one invoice on WhatsApp today."
- Record each session in the shop's Activity tab note: date, who, outcome, what blocked.

## 7. Things to improve in the product (so the call gets shorter)
1. A pre-flight self-check on the Connect page ("Is your number on the Business app? yes/no") before the popup opens.
2. A "Book your WhatsApp setup" step right after payment that offers two time slots.
3. Show the six templates' approval state on the shop's Activity tab so CS sees it without opening Meta.
4. Add `whatsapp_connected` and `whatsapp_first_send` as activity keys (already in the plan) so "Growth paid, not connected" is automatic.

# Price list on request (WhatsApp PDF, framed as Utility)

Started 2026-10-08. See also `docs/PLAN-customer-ordering.md` and the memory rule
`whatsapp-promotional-cost-rule`.

## Why this shape

A catalog PDF pushed to a customer is promotional, so Meta bills it as Marketing. A PDF the customer
**asked for** is a reply to a request, which is what Utility is for. So the message is only ever sent
because the customer pressed "Send me the price list on WhatsApp" on the catalog page. The shop never
sends it to a list. Each firm sends from its own WhatsApp number (decided 2026-10-08).

Honest limit: Meta decides the category from the message, not from our label. If it reclassifies the
template as Marketing we still send it (the customer asked), and the monthly cap and counter below keep
the cost visible and bounded. Template approval and billing are per WhatsApp account, not per number, so
a rejection or reclassification is shared; the wording must be a true reply to a request.

## What is built (nothing yet; this is the plan)

1. **Which PDF.** A share link may carry `pdf_series_id`, the series of one of the shop's customer
   catalogs (the PDFs made from Items). The customer always gets the **latest version** of that series,
   so rebuilding the catalog updates what is sent. No PDF chosen = no button on the public page.
2. **Public page.** A "Send me the price list on WhatsApp" button (only when the link has a PDF) opens a
   two-field form (name, phone, the same checks as ordering). Result line: "On its way. It will arrive
   from <shop> on WhatsApp."
3. **Endpoint.** `POST /api/public/catalog/{token}/price-list`: rate limited per IP and per phone, once
   per phone per link per 24 hours (a repeat answers "already sent", sends nothing), refuses when the
   firm has no WhatsApp set up or its monthly cap is reached (the customer is told to ask the shop).
4. **Message.** New template `price_list_requested`, language English, header = document (the PDF).
   Body: `Hi {{1}}, here is the price list you requested from {{2}}. It is attached as a PDF. If you have any questions, just reply to this message.`
   Samples: `Ramesh`, `Kumar Steel House`. Category requested: Utility. No button.
5. **Record and cap.** Table `price_list_request` (who, which link and catalog version, status
   `pending|sent|failed`, the WhatsApp message, error). `tenant.price_list_monthly_cap` (default 200).
   The shop sees "Price lists sent this month: N of 200" on the share-links panel.
6. **Consent.** A request is a one-off, explicit ask, so it needs no standing opt-in. It does NOT change
   `party.wa_catalog_consent` (the parked `0054` work): that stays for the day the shop wants to send
   unrequested lists.

## Shop side

- `ShareLinkDialog`: optional "PDF price list customers can request" picker (latest customer catalogs).
- `ShareLinksPanel`: per link, "N requests" and the monthly count line.
- `OrderSettingsCard` (Firm page): the monthly cap.

## Open (small, defaults chosen)

- Language is English only for now (the Hindi / Bengali question from the consent thread is unchanged).
- Default cap 200 a month per firm; change on the Firm page.

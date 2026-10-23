# Google Play listing — Metal ERP (listed as "Tally+")

Everything Play Console asks for that is not code, ready to paste. Items marked **CONFIRM** are claims only
the business can verify; do not submit them unchecked. Package `in.fleekfinance.metalerp`.

## Links to enter

| Field | Value |
|---|---|
| Privacy policy | **https://fleekfintech.com/privacy-policy/** (live, version 2; Section 12 covers Metal ERP) |
| Account deletion request page | https://metal.fleekfinance.in/delete-account |
| Website | https://fleekfintech.com |
| Contact email | contact@fleekfinance.in |
| Contact phone | +91 8956506362 |

The policy of record is the versioned WordPress page (source: `fleek-infra/wordpress-corp/legal/privacy-policy.html`, Section 12).
`/delete-account` lives in the app ([LegalPages.tsx](../web/src/pages/public/LegalPages.tsx)); the policy links to it.

## Main store listing

**App name** (30 max): `Tally+`  — see the trademark note at the bottom.

**Short description** (80 max):
`Billing, collections and customer orders for metal and bartan shops.`

**Full description** (4000 max):

```
Run your metal or bartan shop from your phone.

Billing
• Make invoices quickly, with item search that understands Hindi and trade spellings
• Weighment slips and weight-based rates for metal sold by the kilo
• Share the invoice PDF on WhatsApp in one tap

Collections
• See who owes you and for how long, in simple age buckets
• Record payments against the right bills
• Send payment reminders and account statements on WhatsApp — you approve each one

Customer ordering
• Share a catalog link with your customers
• They pick items and place an order, no app or login needed
• You review the order and turn it into an invoice

Items and price lists
• Add item photos with your phone camera
• Turn a supplier's PDF price list into your own catalog, with your margin
• Print barcode labels

Works with Tally
• Optional companion for TallyPrime: keep parties and items in step, and send finished invoices to Tally
• Your Tally company backup can be kept safe in the cloud

Made for Indian shops, with data kept separate for each business.

Tally and TallyPrime are trademarks of Tally Solutions Pvt. Ltd. This app is not affiliated with or endorsed by Tally Solutions.
```

**CONFIRM** before publishing: every feature above is live for new shops (several are switched on per firm), and
"Hindi" search, "barcode labels" and the Tally sync match what you want to promise. The description must not claim
GST invoicing — it is not built yet.

## Graphics (not code; still needed)

| Asset | Spec | State |
|---|---|---|
| App icon | 512×512 PNG | ring-T icon in `web/public/icons/icon-512.png` (final) |
| Feature graphic | 1024×500 PNG/JPG | not made |
| Phone screenshots | at least 2, 16:9 or 9:16 | not taken — use a **demo shop**, never a real firm's data |

## App content declarations

| Question | Answer |
|---|---|
| App category | Business |
| Contains ads | No |
| Target audience | 18 and over (business tool) |
| Content rating | Complete the IARC questionnaire honestly; expected result is "Everyone" |
| Financial features | Declare "none" **only if** the form's definitions fit: the app records invoices and payments but does not lend, move money, trade or hold funds. Read each option, do not tick by habit |
| Government / health / news app | No |
| App access | Reviewers need a login: create a **demo firm** with sample data and give its email and password. **CONFIRM** it exists |
| Account deletion | Web page: https://metal.fleekfinance.in/delete-account. In the app: Firm profile → "Account and data" → "request account deletion" (owner only) |

## Data safety form

Rebuilt 2026-10-23 from the code (sign-up, the firm profile, the party/invoice/photo/upload features) after the Fleek CRM form review.
Every ticked type is **collected, not shared, not processed ephemerally** (it is all stored).

Account creation: **Username and password** only (register with firm name, email, password; no Google, no OTP). Account deletion URL and data
deletion URL: `https://metal.fleekfinance.in/delete-account`. Privacy policy: `https://fleekfintech.com/privacy-policy/` (Section 12).

| Type | Required or optional | Purposes | What it is |
|---|---|---|---|
| Email address | Required | App functionality, Account management | the login |
| User IDs | Required | App functionality, Account management | the login account identifiers |
| Name | Optional | App functionality | customers' and suppliers' names the shop enters (sign-up asks only for a firm name) |
| Phone number | Optional | App functionality | customers', suppliers' and the firm's phone; also the WhatsApp recipient |
| Address | Optional | App functionality | the firm's and parties' addresses |
| Other info | Optional | App functionality | PAN and GSTIN |
| User payment info | Optional | App functionality | the firm's bank account number, IFSC and UPI ID, printed on invoices |
| Purchase history | Required | App functionality | the items on sales invoices and purchase bills |
| Other financial info | Required | App functionality | invoices, payments, balances, opening balances |
| Other in-app messages | Optional | App functionality | the WhatsApp messages a shop sends (invoices, reminders, statements, order updates) and their delivery status |
| Photos | Optional | App functionality | item photos taken or chosen from the gallery |
| Files and docs | Optional | App functionality | supplier PDFs and price lists, purchase bills, spreadsheets, Tally files |
| **Voice or sound recordings** (Audio files) | Optional | App functionality | the push-to-talk **voice line** on the invoice screen: the browser asks for the microphone, the audio streams through our server to Deepgram for speech-to-text, and the words fill the invoice line. We do not store the recording |

Leave unticked: Location, Contacts, Calendar, Health and fitness, Videos, Music and other audio files, Emails, App activity (no analytics runs in this app), Device or
other IDs, Crash logs and Diagnostics.

**CONFIRM** before submitting:
- **Audio and Deepgram.** Our server does not store the audio (it relays the stream and keeps only the resulting text on the invoice line), but the Deepgram
  call sets no model-improvement opt-out, so Deepgram's own retention is not under our control. If Deepgram may keep or learn from the audio, declare
  it **shared** and not ephemeral. If you set `mip_opt_out=true` on the call and confirm Deepgram's retention, it is a service provider acting for us
  and "not shared" is defensible. Only call it "processed ephemerally" if you are confident neither we nor Deepgram retain it. Also: the call sends the
  shop's most-used item names to Deepgram as recognition hints.
- If document reading is switched on in production, the uploaded file goes to Anthropic; same reading (service provider vs shared).
- WhatsApp (Meta) delivers the messages a shop asks for; same reading as the CRM form (processing, not sharing).
- "Purchase history" is a judgment call: the invoice line items are the shop's business records, not a consumer's shopping history. Declaring it
  costs nothing and avoids an under-declaration.

Security practices: **encrypted in transit** Yes (HTTPS); **users can request deletion** Yes (the page above); **independent security review** No.

## Open items before submitting for production

1. **Deploy `/delete-account`** (Metal ERP, with the Firm profile link) and open it in a browser. The policy page is already live (version 2).
   The policy's header comment, with the note "DRAFT: counsel to review", is visible in view-source; drop it at the next edit.
2. **In-app deletion path — built.** Firm profile has an "Account and data" card with the privacy-policy link (all users) and "request account deletion"
   (owner only), which opens `/delete-account`; the policy link already points at the WordPress page. Deletion itself is a manual,
   emailed request, as the page says.
3. **Third-party services in the policy.** The policy names Anthropic and Deepgram "if a shop switches on
   document reading or voice entry". Check whether either is on in production; delete the line if neither ever is.
   Google Fonts and the Facebook SDK are real and listed.
4. **Retention promise.** The policy says data is deleted "within one month" of a deletion request — copied from your
   published CRM policy. **CONFIRM** you can honour it for Metal ERP, including the Tally backups in R2.
5. **Legal review.** The text is a careful first draft from the code, not legal advice. Have your adviser read it,
   especially the controller/processor split and the DPDP Act wording. It names no grievance officer; add one if
   your adviser says the Act requires it.
6. **Demo firm and screenshots** (above).
7. **The name.** "Tally+" uses Tally Solutions' trademark as part of your own brand. Google can reject or remove an
   app whose name suggests it comes from, or is endorsed by, another company. The store title can be changed at any
   time without a new build; a safer title is "Metal ERP for Tally" or similar, with Tally named only in the text.

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
| App icon | 512×512 PNG | placeholder "M" exists in `web/public/icons/icon-512.png` — replace with real branding |
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

Answers drawn from what the code stores and sends (see the privacy policy). Play asks about data the **app**
collects; data that a shop owner enters about its own customers is still collected by us, so it is declared.

| Data type | Collected | Shared | Purpose | Notes |
|---|---|---|---|---|
| Name | Yes | No | App functionality, account management | users and the shop's customers/suppliers |
| Email address | Yes | No | Account management | login emails |
| Phone number | Yes | No\* | App functionality | customers, suppliers; also the WhatsApp recipient |
| Physical address | Yes | No | App functionality | optional customer/supplier address |
| Other financial info | Yes | No | App functionality | invoices, payments, balances (not card/bank details) |
| Photos | Yes | No | App functionality | item photos the user adds |
| Files and docs | Yes | No | App functionality | uploaded PDFs, price lists, Tally files |
| Location | No | — | — | |
| Contacts (phone book) | No | — | — | |
| Audio | No | — | — | microphone is not requested |
| Device or other IDs | No | — | — | no advertising ID, no analytics SDK |

\* Messages are sent through WhatsApp (Meta) because the user asks for them. Google's form treats a
service provider acting for you as "processing", not "sharing"; **CONFIRM** that reading against Google's current
definition, because answering "shared" is the safer side if in doubt.

Security practices:
- **Data is encrypted in transit:** Yes (HTTPS everywhere).
- **Users can request deletion:** Yes — the deletion page above.
- **Follows the Families policy / independent security review:** No.

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

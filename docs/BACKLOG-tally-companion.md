# Tally companion: epics and user stories

Opened 2026-10-30. Companion to [GAP-REGISTER-tally-companion.md](GAP-REGISTER-tally-companion.md), which holds the gaps (A1 to G6) and the decisions (D-1 to D-11). This file turns them into work. Nothing here may contradict a decided row in the register; if it must, change the register first and say why.

## How we keep track

1. **Every story names the gaps and decisions it closes.** The traceability tables at the end must show every gap and every decision covered by at least one story. If a row is empty, the plan is incomplete.
2. **Status lives here, closure lives in the register.** A story moves Open, Building, Done. A gap is marked closed in the register only when all its stories are Done.
3. **Commit messages and PR titles carry story IDs** (for example `S1.2`).
4. **New ideas become new stories or new gap rows.** They never replace existing ones.
5. **Live checks are stories too** (spikes), because several gaps are expected Tally behaviour, not yet reproduced.
6. **Fleek-side stories start with a parity table** (the rule from the control-plane work): read how Fleek does it first, no new route, variable, secret or button Fleek lacks.
7. The user commits and pushes. Nobody else does.

Sizes are rough and relative: S about a day, M two to three days, L about a week or more, XL needs splitting before it starts.

## Shared definition of done

- Backend tests for the story pass; the affected existing tally tests still pass (`test_tally_connector`, `test_invoice_tally_push*`, `test_inward_tally_xml`, `test_tally_self_serve`, `test_tally_item_import`, `test_tally_party_import`). Use a private `DATABASE_URL` sqlite file and `-p no:randomly`.
- A migration, where there is one, has a backfill that leaves every existing firm behaving as today, and is checked against a copy of production shape (Postgres), not only SQLite.
- Any new read of company data goes through the visibility filter from S1.7 (once it exists).
- Web: typecheck and lint clean; a mockup agreed before UI coding.
- The register row and this file are updated in the same change.

## Epics

| Epic | Theme | Phase | Closes |
|---|---|---|---|
| EP0 | Company safety | P0 | A1, A2 (part), B3, F1, C3 (spike) |
| EP1 | Many companies per firm | P1 | G2, G3, A2, B1, B5, C5, D-8 masters rule |
| EP2 | Routing and documents | P2 | G4, G5, D1, D2, B2 (part), B4, C3, D-5, D-6, D-7 |
| EP3 | Account and people | P3 | G1, A3, A4, E4, G6, D-1, D-2, D-3, D-11 |
| EP4 | Voucher truth | P4 | C1, C2, D-8 |
| EP5 | GST | P5 | C4 |
| EP6 | Stock and catalog | P6 | E2, E3, D3, D4, B2 (rest), E1 parked, D-9, D-10 |

Order: EP0 first. EP1 next. EP2 needs EP1. EP3 can start in parallel with EP2 once EP1's schema is in (the account table does not depend on routing). EP4 needs EP1 (per-company links) and the S0.1 findings. EP5 needs EP2. EP6 needs EP1 and S0.1.

---

## EP0: Company safety (P0)

Goal: a voucher can never land in the wrong Tally company, a retry can never create a duplicate, and a unit mismatch is caught before the push, not after.

### S0.1 Spike: live two-company check (L-shaped, do first)
**As** the team, **we want** to reproduce the expected Tally behaviours on a dev Tally with two companies open, **so that** the later stories are built on facts.
- Closes/informs: A1, C3, E2, F1, B3, C1.
- Acceptance:
  - A1: post our current sales XML while company B is open and company A is the intended one; record which company receives it.
  - F1: post the same voucher twice (same `METALERP_REF`, same number); record the result; find a gateway request that finds a voucher by the UDF reference.
  - C3: post with the voucher type on auto-numbering and on manual; record what Tally does with our `VOUCHERNUMBER`.
  - B3: post a line with a unit that does not exist; record the error text.
  - E2: post a sale against an item with no stock; record the result with negative stock allowed and disallowed.
  - C1: capture what the Import response gives for a voucher key (`LASTVCHID`, `MASTERID`, `GUID`).
  - How to read the open company's GUID and name through the gateway.
  - Findings written into the register's "Not verified yet" section, replacing guesses.
- Note: the dev Tally is the unlicensed eval, so voucher dates must be the 1st or 2nd of a month.
- Size: M.

### S0.2 Every voucher push names its company
**As** a shop owner, **I want** each voucher posted into the company it was meant for, **so that** it never lands in another set of books.
- Closes: A1.
- Acceptance:
  - Sales and purchase Import-Data XML carry `SVCURRENTCOMPANY` from the company row, the same way the catalog push already does.
  - Unit tests assert the tag is present and XML-escaped for names with `&`, quotes and non-ASCII characters.
  - A company with an empty name is a push blocker, not an empty tag.
  - Existing push tests updated; no behaviour change for a single-company shop.
- Touches: `services/invoices/tally_xml.py`, `services/inward/tally_xml.py`, `services/tally/jobs.py`, `push_readiness.py`.
- Size: S.

### S0.3 Agent checks the open company before posting
**As** a shop owner, **I want** the agent to refuse a push when the wrong company is open, **so that** a mistake is caught before it posts.
- Closes: A1, A2 (part). Depends: S0.1, S0.4.
- Acceptance:
  - Before any import, the agent reads the company the gateway will use and compares it to the expected company (GUID when known, name otherwise).
  - On mismatch the job ends with a clear status (`wrong_company`) and plain text: which company was expected and which is open. It does not retry as "Tally not ready".
  - The same check guards pull and capture, so a masters pull cannot stage another company's ledgers.
  - Agent unit tests cover match, mismatch, and no company loaded. New agent release needed (version, sign, canary-pin one shop, promote), per the delivery-hardening flow.
- Touches: `tally-agent/TallyAgent/Modules/TallyMastersModule.cs`, `Tally/TallyGatewayClient.cs`, `routers/tally_agent.py`, `schemas_tally_agent.py`.
- Size: L (includes agent release).

### S0.4 Store the company's GUID and books-from date
**As** the system, **I want** to know each Tally company's GUID, **so that** a rename or relink does not silently break links.
- Closes: A2 (part).
- Acceptance:
  - `tally_company` gains `company_guid` and `books_from` (nullable). Filled from the first successful pull or a "verify company" action.
  - The Tally page shows the stored name and GUID, and warns when the open company's name changed but the GUID matches (a rename), or the GUID differs (a different company).
  - Migration is additive; existing rows stay null until verified.
- Touches: `models/tally_connector.py`, new migration, `routers/tally.py`, `schemas_tally.py`, web Tally page.
- Size: M.

### S0.5 Unit mapping and a unit blocker
**As** a shop owner, **I want** our unit names to map to my Tally's unit names, **so that** a voucher is not rejected for a unit I never meant to change.
- Closes: B3. Depends: S0.1.
- Acceptance:
  - Each company has a unit map (ours to Tally's, for example `nos` to `Pcs`), editable, with a sensible default.
  - Known units are read from the masters pull and offered as choices.
  - The push readiness check blocks an invoice or bill that uses a unit with no Tally equivalent and says which.
  - Option to create a missing unit at push time (same path the catalog push uses), off by default.
  - The `nos` stored, `pcs` display-only rule is respected.
- Touches: `models/tally_connector.py`, `services/tally/push_readiness.py`, both voucher serializers, `tally_items.py`, web.
- Size: M.

### S0.6 Safe retry: no duplicate vouchers
**As** a shop owner, **I want** a retry after a timeout never to create a second voucher, **so that** my books are not doubled.
- Closes: F1. Depends: S0.1.
- Acceptance:
  - Before re-enqueueing a push for an invoice or bill whose earlier job expired or errored with "may already be saved", the system asks Tally (through the agent, read-only) whether a voucher with that `METALERP_REF` exists.
  - If it exists: the job is marked ok and the link is written; no second voucher.
  - If the check cannot run (agent offline), the shop is told to look in Tally, as today.
  - Test with a fake gateway that already holds the voucher.
- Touches: `services/tally/jobs.py`, `voucher_capture.py` style read request, agent, `routers/tally.py` retry.
- Size: L.

---

## EP1: Many companies per firm (P1)

Goal: a firm has several live Tally companies, each with its own links, ledger map, staging and visibility, and nothing changes for a firm with one company.

### S1.1 Company registry: many per firm
**As** an owner, **I want** to add more than one Tally company to my firm, **so that** GST and Estimate books both work.
- Closes: G2. Decisions: D-6 (field), D-7 (field).
- Acceptance:
  - Drop `uq_tally_company_tenant`. Add `kind` (`gst`, `estimate`, `branch`, `fy`, `other`), `is_default`, `series_prefix`, `document_label`, `include_in_reminders`, `visible_roles`, `status`.
  - Exactly one default per firm, enforced in the database (partial unique index) and in code.
  - Migration marks each existing company `is_default`, kind `gst` if the firm has GST on else `other`, `include_in_reminders` true, `visible_roles` null (everyone). Existing shops behave as before.
  - `get_tally_company(tenant)` is replaced by `get_default_company` and `get_company(id)`; all 15 call sites in 6 files are moved, none left on the old single lookup.
- Touches: `models/tally_connector.py`, migration, `services/tally/push_readiness.py`, `routers/tally.py`, `routers/tally_self_serve.py`, `routers/inward.py`, `services/inward/approve.py`, `services/catalog/tally_items.py`.
- Size: L.

### S1.2 `tally_link` per company
**As** the system, **I want** one customer or item to hold a separate Tally link in each company, **so that** the same customer can exist in both books.
- Closes: B1.
- Acceptance:
  - `tally_link` gains `company_id`; unique on (company, entity_type, entity_id) and (company, entity_type, tally_guid). Backfill sets `company_id` to the firm's default company.
  - One helper reads a link for (entity, company). The 21 reads of `party.tally_guid` or `item.tally_guid` across 10 files go through it, with the legacy column as a read-only fallback during transition.
  - Imports and pushes write the link, not the legacy column. A test proves no new write to the legacy columns.
- Touches: model, migration, `routers/parties_import.py`, `routers/items_import.py`, `routers/catalog_review.py`, `services/tally/jobs.py`, `push_readiness.py`, `tally_xml.py`, `tally_items.py`, `item_filter.py`, `schemas_item.py`.
- Size: L.

### S1.3 GUID-first push, name stored per link
**As** an owner, **I want** a renamed customer or item to keep working, **so that** a rename on either side does not break a push.
- Closes: B1. Decision: D-8.
- Acceptance:
  - The link stores the Tally-side name seen at the last pull or push (`tally_name`).
  - A push uses the link's `tally_name`, not our current name, once linked. The first push of an unlinked record uses our name and then records it.
  - When our name and the link's Tally name differ, the record shows "name differs in Tally" with a choice to keep, or to rename in Tally on next push.
  - Test: rename the party here after linking; the voucher still carries the Tally name.
- Size: M. Depends: S1.2.

### S1.4 Company management screen and API
**As** an owner, **I want** to see and manage my Tally companies, **so that** I know which one is which and which is the default.
- Closes: G2 (UI). Decisions: D-4.
- Acceptance:
  - Mockup agreed first.
  - List, add, rename, set default, set kind, set series prefix, set reminders switch, set who can see it, disable.
  - The Fleek-admin proxy (`control_plane_firm.py`) exposes the same, with the parity table written first.
  - A firm with one company sees today's Tally page, with no extra clutter.
- Touches: `routers/tally.py`, `schemas_tally.py`, `routers/control_plane_firm.py`, web Tally page and Firm page.
- Size: L.

### S1.5 One ledger map per company; retire `TallyLedgerConfig`
**As** a maintainer, **I want** one source for ledger names, **so that** inward and sales cannot disagree.
- Closes: B5.
- Acceptance:
  - Inward approve and push read `ledger_map` of the document's company.
  - A migration copies each firm's `tally_ledger_config` into its default company's map where the map lacks the key; conflicts are listed in the migration output, not overwritten.
  - After one release, `TallyLedgerConfig` and its 10 reads are removed.
- Size: M.

### S1.6 Per-company pull, staging and in-flight guards
**As** an owner, **I want** a pull for one company never to block or overwrite another's, **so that** I can work in both.
- Closes: C5.
- Acceptance:
  - `staging_tally_party` and `staging_tally_item` carry `company_id`; the one-uncommitted-batch rule becomes per company.
  - `assert_no_pull_in_flight`, the stock-check and push guards key on company.
  - Review and commit screens say which company the batch came from.
- Touches: `models/tally_import.py`, `services/tally/jobs.py`, `staging.py`, `routers/parties_import.py`, `routers/items_import.py`.
- Size: L. Depends: S1.1.

### S1.7 Per-company visibility filter
**As** an owner, **I want** to hide a company from some roles, **so that** the estimate book is not shown to people who should not see it.
- Closes: G3. Decision: D-4.
- Acceptance:
  - One function, `visible_companies(user)`, in one module. Rules: owner always sees all; null `visible_roles` means everyone; otherwise the user's role must be listed. Roles that exist: owner, accountant, viewer, counter, weighbridge, rate_desk.
  - Applied to: company list and detail, sync jobs, push status, agent job views, Fleek-admin firm Tally endpoints.
  - A test per role and per surface; a "leak checklist" in the PR listing each place company data is read.
  - Every later story that adds a read of company-scoped data (invoice lists, ledgers, ageing, reminders, statements, imports) must call it; this is part of their acceptance.
- Size: M. Depends: S1.1.

### S1.8 Relink for a new financial year
**As** an owner, **I want** to move to the next year's Tally company without losing my links, **so that** year end does not break the connection.
- Closes: A2. Depends: S0.4, S1.2.
- Acceptance:
  - A guided "link next year's company" action: pick the new company, pull its masters, auto-match parties and items to existing records by name and GSTIN, show the unmatched for review, then write links for the new company.
  - The old company stays linked and, if the owner wants, active (backdated entries). Its links are untouched.
  - Test with two companies sharing most ledgers.
- Size: L.

### S1.9 Masters ownership on re-pull
**As** an owner, **I want** a Tally pull never to overwrite my phone, consent, prices or credit days, **so that** what I maintain here stays mine.
- Closes: D-8 (masters rule).
- Acceptance:
  - On a pull for a linked record, Tally-owned fields (ledger name via the link, group, GSTIN when ours is blank) update or flag; our commercial fields (phone, email, consent, link token, credit days, opening balance, prices, photos, catalog flags) are never changed by a pull.
  - A test lists the protected fields and fails if a new field is written by the importer without being added to either list.
  - A change in a Tally-owned field that conflicts with ours shows a review flag instead of overwriting.
- Size: M. Depends: S1.2.

---

## EP2: Routing and documents (P2)

Goal: every document knows which company it belongs to, with the right number, tax block and label; masters are created in Tally only when missing.

### S2.1 Invoice carries its company
- Closes: G4. Decision: D-5.
- **As** a user, **I want** each bill tied to one company at draft, **so that** it posts to the right books.
- Acceptance: `invoice.tally_company_id` (nullable for a firm with no companies); default by the order: explicit, party default, document type, firm default; locked at finalize and shown on the invoice; no bill posts to two companies. Migration backfills finalized invoices to the default company. Uses the S1.7 filter.
- Size: M. Depends: S1.1.

### S2.2 Party default company
- Closes: G4. **As** an owner, **I want** to set which company a customer's bills go to by default.
- Acceptance: `party.default_company_id`, editable on the party, validated to a company the editing user can see, cleared if the company is disabled.
- Size: S.

### S2.3 Company switch in the invoice editor
- Closes: G4. **As** a counter user, **I want** to pick the company on a bill when I need to.
- Acceptance: switch appears only when the firm has more than one company visible to the user; shows only visible companies; changing company on a draft re-runs defaults for series and label; mockup first.
- Size: M.

### S2.4 Series and label per company
- Closes: G5, C3. Decision: D-7.
- Acceptance:
  - Number sequence, prefix and document label move from the tenant to the company. Existing firms get their current sequence copied to the default company so numbers continue unbroken.
  - Our sales use our own series (own voucher type or prefix) in that company; the voucher type is created in Tally through the import path if missing.
  - Onboarding check confirms the target voucher type accepts manual numbers and tells the shop what to switch if not.
  - Imported Tally vouchers never touch our sequence.
- Size: L. Depends: S0.1, S2.1.

### S2.5 Push uses the document's company
- Closes: G4. **As** the system, **I want** every push to go to the document's own company.
- Acceptance: readiness, enqueue, job and link use the invoice's or bill's company; ledger map, units and series are that company's; sales and purchase.
- Size: M. Depends: S2.1, S1.5.

### S2.6 Inward bill carries its company
- Closes: D1. Decision: D-11 (GSTIN routing comes with EP3, S3.9).
- Acceptance: `inward_bill.tally_company_id`, default by firm default or user choice; visible filter applied; purchase push uses it.
- Size: M.

### S2.7 Check Tally before creating a supplier or item
- Closes: D1, B2 (part).
- Acceptance: the purchase push first compares supplier and item names and GUIDs against the company's known masters (same check-Tally step the catalog push uses); creates only what is missing; a near-match is flagged for the user instead of duplicated.
- Size: L.

### S2.8 Orders follow the party's company
- Closes: D2. Acceptance: an order turned into an invoice takes the party default company; customer-facing pages and WhatsApp show the firm name only, never the company.
- Size: S. Depends: S2.1, S2.2.

### S2.9 Party roles as a set
- Closes: B4. Acceptance: a party may be both customer and supplier; group scoping (`debtors_parent`, `creditors_parent`) is per company; resolve-party ladder order is not changed.
- Size: M.

### S2.10 Wrong-company correction (interim)
- Closes: D-5 (guidance). Acceptance: finalized invoice in the wrong company shows plain guidance (credit note plus fresh invoice; until EP4 S4.5, fix it in Tally). The full flow is S4.5.
- Size: S.

### S2.11 Combined reminders with a per-company switch
- Closes: D-6. Depends: S2.1, S1.7.
- Acceptance: a reminder totals the party's overdue bills across companies marked `include_in_reminders` and visible to the approver; owner-only companies excluded by default; itemised by invoice number; the approve screen lists which companies each reminder covers; sent from the account's number with the firm name.
- Size: L.

---

## EP3: Account and people (P3)

Goal: one login, many firms, one plan, one WhatsApp, a shared item code counter, and agents that serve several firms.

### S3.1 Account table
- Closes: G1. Acceptance: `account` table; `tenant.account_id`; `fleek_org_id` moves to the account; migration creates one account per existing tenant; Fleek sync reads the account. Fleek parity table first.
- Size: L.

### S3.2 Membership and firm switcher
- Closes: A3. Acceptance: `membership(user, tenant, role)`; backfilled from `app_user.tenant_id`; mobile stays globally unique; token carries the active firm and a switch call re-issues it; the web shows a switcher only when the user has more than one firm; role is per firm.
- Size: XL (split: schema and backfill, token and switch, web).

### S3.3 Agent registry at account level
- Closes: A4. Acceptance: agents belong to the account; each agent reports the companies it can see; the owner maps a seen company to a firm; one company can only be mapped to one firm.
- Size: L. Depends: S3.1, S0.3.

### S3.4 Account-level item code counter
- Closes: E4. Decision: D-3. Acceptance: `CodeSequence` keyed by account; starts above the highest code across the account's firms; existing codes untouched; `catalog_product` stays unique per tenant; a test shows two firms in one account never issue the same code.
- Size: M. Depends: S3.1.

### S3.5 Plan and entitlement at account level
- Closes: D-1. Acceptance: entitlement and Free limits read at account; a firm limit is a settable value per plan; creating a firm past the limit is refused with a plain message; the add-on rule is not built (pricing decides); no change to current plans.
- Size: L. Depends: S3.1.

### S3.6 WhatsApp connection at account
- Closes: D-2. Acceptance: connection and send gate at account; each firm has an optional `whatsapp_connection_id` (null means the account's), not shown in the UI yet; the firm name is in the message text.
- Size: M. Depends: S3.1.

### S3.7 Create another firm under an account
- Closes: D-11. Acceptance: the owner adds a firm (limit from S3.5); separate GSTIN, parties, items, catalog; optional "start from the other firm's settings"; Fleek control plane informed (parity table first).
- Size: L.

### S3.8 Rename the agent record
- Closes: G6. Acceptance: `BackupShop` becomes `agent` in code and docs; table rename migration; public routes unchanged or aliased; the word "shop" means the business only.
- Size: M. Do after S3.3 to avoid renaming twice.

### S3.9 Inward bill routes to the firm by buyer GSTIN
- Closes: D-11 / D1. Acceptance: with several firms under one account, the bill's buyer GSTIN selects the firm; no match asks the user; never guesses silently.
- Size: M. Depends: S3.2.

---

## EP4: Voucher truth (P4)

Goal: receipts, credit notes and imported history flow both ways under the originator-owns rule.

### S4.1 Real voucher key
- Closes: C1. Depends: S0.1. Acceptance: store a real Tally key for each pushed voucher; `pushed:<number>` fallback removed or flagged unreliable.
- Size: M.

### S4.2 Voucher import into native rows
- Closes: C2. Decision: D-8. Follows `PLAN-tally-voucher-import-and-party-prices.md`, per company, whole not in pieces.
- Split: S4.2a parser and fixtures; S4.2b apply sales as native invoices (`source=tally_import`); S4.2c apply receipts with bill allocations; S4.2d opening balances and bills receivable; S4.2e visibility filter applied to imports.
- Size: XL (split above).

### S4.3 Reconcile and flags
- Closes: C1. Decision: D-8. Acceptance: for each voucher we pushed, flag "missing in Tally" and "changed in Tally"; never auto-delete or overwrite; a daily or on-demand run.
- Size: L. Depends: S4.1, S4.2a.

### S4.4 Receipt push
- Closes: C2. Decision: D-7. Acceptance: a recorded payment pushes as a Receipt voucher in our series with bill-wise allocation; ledger from the payment mode and the company's map.
- Size: L.

### S4.5 Credit notes
- Closes: C2. Decision: D-5. Acceptance: issue a credit note against a finalized invoice (we never cancel finalized); pushes to the same company; supports the wrong-company correction.
- Size: L.

---

## EP5: GST (P5)

Closes C4. Needs its own discovery story before breakdown, because it is the largest piece.

### S5.0 GST discovery
- Acceptance: written scope for tax lines, HSN, place of supply, rounding, GST ledgers in the company map, tax invoice document, and which GST companies we support first; decides splits. GSTIN stays on the firm (D-11).
- Size: M. Then break into stories before building.

---

## EP6: Stock and catalog (P6)

### S6.1 Stock report read
- Closes: E2. Decision: D-9. Depends: S0.1. Acceptance: agent reads closing stock per item for a company; backend stores it with an as-of time; report shape confirmed live (see `tally-stksum-vs-masters`).
- Size: L.

### S6.2 Show stock with its age
- Closes: E2. Acceptance: item and editor show stock "as of" a time, per company; stale or unknown is shown as such; billing never blocks.
- Size: M.

### S6.3 Negative-stock warning
- Closes: E2. Acceptance: warn only; switchable off per shop; the explained error when Tally itself rejects a push.
- Size: S.

### S6.4 One creator for Tally stock items
- Closes: D4, B2. Acceptance: written rule for `SupplierCatalogItem`, `CatalogProduct` and `Item`; one code path creates Tally stock items; duplicates by name or part number blocked.
- Size: M.

### S6.5 Catalog "In stock" from stock
- Closes: E3. Status: Parked until you ask. Acceptance when taken: reads S6.1 numbers per company.

### S6.6 Price points per company
- Closes: D3. Acceptance: `supplier_price_point` carries a company where it matters; no change for one company.
- Size: S.

### S6.7 Barcode aliases and scan input
- Closes: E1. Decision: D-10 (internal code only). Status: Parked. Revisit trigger: a pilot shop asks for scan-at-counter or EAN packs; then aliases with a unit multiplier, scan-in at billing first.

---

## Traceability

### Gaps to stories (every gap appears)

| Gap | Stories | | Gap | Stories |
|---|---|---|---|---|
| A1 | S0.1, S0.2, S0.3 | | C5 | S1.6 |
| A2 | S0.3, S0.4, S1.8 | | D1 | S2.6, S2.7, S3.9 |
| A3 | S3.2 | | D2 | S2.8 |
| A4 | S3.3 | | D3 | S6.6 |
| B1 | S1.2, S1.3 | | D4 | S6.4 |
| B2 | S2.7, S6.4 | | E1 | S6.7 (parked) |
| B3 | S0.1, S0.5 | | E2 | S0.1, S6.1, S6.2, S6.3 |
| B4 | S2.9 | | E3 | S6.5 (parked) |
| B5 | S1.5 | | E4 | S3.4 |
| C1 | S0.1, S4.1, S4.3 | | F1 | S0.1, S0.6 |
| C2 | S4.2, S4.4, S4.5 | | G1 | S3.1 |
| C3 | S0.1, S2.4 | | G2 | S1.1, S1.4 |
| C4 | S5.0 and its splits | | G3 | S1.7 (and the filter step in every later story) |
| | | | G4 | S2.1, S2.2, S2.3, S2.5 |
| | | | G5 | S2.4 |
| | | | G6 | S3.8 |

### Decisions to stories (every decision appears)

| Decision | Stories |
|---|---|
| D-1 plan per account | S3.5 |
| D-2 WhatsApp at account | S3.6 |
| D-3 account code counter | S3.4 |
| D-4 per-company visibility | S1.1, S1.4, S1.7, and the filter step in S2.1, S2.2, S2.3, S2.11, S4.2e |
| D-5 one bill, one company | S2.1, S2.10, S4.5 |
| D-6 combined reminders | S1.1 (field), S2.11 |
| D-7 series per origin | S1.1 (field), S2.4, S4.4 |
| D-8 originator owns | S1.3, S1.9, S4.2, S4.3 |
| D-9 Tally owns stock | S6.1, S6.2, S6.3 |
| D-10 internal code only | S6.7 (parked) |
| D-11 firms per legal entity | S3.7, S3.9 |

## Suggested first sprint

1. S0.1 spike (needs the dev Tally with two companies).
2. S0.2 company on every push (small, safe, independent of the spike).
3. S0.4 company GUID and books-from.
4. S1.1 schema work can start in design while S0.3 and S0.5 wait for spike results.

## Status log

| Date | Change |
|---|---|
| 2026-10-30 | Backlog opened: 7 epics, 48 stories (S4.2 counted once though it splits into five), all Open. |

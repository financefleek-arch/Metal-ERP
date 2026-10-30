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

### S0.1 Spike: live two-company check (DONE 2026-10-30, findings below)
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
- **Findings** (script: scratchpad `spike_s01.py`; companies "Fleek" on port 9000 and "Fleek Test" on port 9001):
  - Tag accepted; naming a company the process has not loaded is refused with `Could not set 'SVCurrentCompany' to 'X'` and nothing is posted (S0.3 must show this in plain words).
  - Duplicate post creates a second voucher (F1). Our `METALERP_REF` UDF cannot be read back; use `REFERENCE`.
  - Unknown unit posts silently with blank quantity and rate (B3, now Critical).
  - Our `VOUCHERNUMBER` is discarded on an auto-numbered Sales type (C3).
  - Negative stock is allowed (E2 is a warning, not a failure).
  - `LASTVCHID` = master id; voucher GUID = company GUID + "-" + 8-digit hex of it (C1).
  - Not reproduced: two companies in one process; a manual-numbering type; a blocking negative-stock setting.

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

### S0.3 Agent checks the open company before posting (BUILT 2026-10-30, uncommitted; needs an agent release)
**As** a shop owner, **I want** the agent to refuse a push when the wrong company is open, **so that** a mistake is caught before it posts.
- Closes: A1, A2 (rest). Depends: S0.1, S0.4. Design and decisions: `docs/DESIGN-agent-company-routing.md`. Scope: ONE gateway (several gateways = S1.10, deferred).
- Built, backend:
  - Every job payload carries `company_guid` beside `company_name` (masters pull, sales, purchase, items, capture, check). Older agents ignore it and keep working by name.
  - New wait status `company_not_open` (kept and retried, then cancelled with "X is not open in TallyPrime. Open now: A, B."). New result `error_code` (`wrong_company`, `company_renamed`, `company_ambiguous`): nothing was sent, so these do NOT leave an unknown outcome (S0.6). A job that only ever waited is no longer flagged unknown either.
  - Checkin carries the companies open in Tally (`tally_companies`, migration 0073 on `backup_shop`). From it each linked company's GUID is learned by itself (exactly one open company with our name), and a rename or a same-named other company is noticed with no one pressing Verify. An empty list changes no verdict. A repeated identical checkin writes nothing.
  - Push is blocked (sales and purchase) while the verdict is `different` or `renamed`, with the reason as the message.
  - Admin: `GET /company/open` (what the agent last saw open) and `POST /company/relink {guid}` (take that company's name and GUID: fixes a rename, or links the right company). Panel shows an "Open in Tally now" list with "Use this company" when the verdict needs attention; waiting text names the company.
- Built, agent (C#): `Tally/OpenCompanies.cs` (the verified Company-collection request + parser), `Tally/CompanyCheck.cs` (pure decision), `TallyGatewayClient.ListCompaniesAsync`, a guard in `TallyMastersModule` in front of pull/capture/push, the open-company list on every checkin, `error_code` on job results. A job that names no company, or a list that cannot be read, behaves exactly as before.
- Tests: backend 14 new (payload, wait, expiry text, hard stops, checkin learn/rename/different/empty/no-rewrite, blocker, relink, relink refused); Tally-related backend set 314 passed, 0 failed. Agent 15 new (parser, decision table, module against a fake gateway: right company, renamed, wrong company, wait then proceed, no company named, checkin list); whole agent suite 60 passed. Web eslint and tsc clean.
- Not covered: several gateways and port discovery (S1.10); a masters pull of a non-default company on another Tally window; an owner-facing relink (the panel is the operator console); catalog item push does not yet use the new push blocker.
- Still to do: agent release (version tag, sign, canary-pin one shop, promote) per the delivery-hardening flow; migrations 0072 and 0073 on Postgres; one run through a real agent against the two dev companies (the agent's parser was tested on XML copied from the live answer, not against the live gateway itself).
- Touches: `services/tally/company_identity.py`, `company_verify.py`, `jobs.py`, `push.py`, `push_readiness.py`, `voucher_capture.py`, `voucher_check.py`, `services/catalog/tally_items.py`, `routers/tally.py`, `routers/tally_agent.py`, `schemas_tally.py`, `schemas_tally_agent.py`, `models/tally_agent.py`, migration 0073, `tally-agent/TallyAgent/*`, web panel.
- Size: L.

### S0.4 Store the company's GUID and books-from date (BUILT 2026-10-30, uncommitted)
**As** the system, **I want** to know each Tally company's GUID, **so that** a rename or relink does not silently break links.
- Closes: A2 (part). Depends: none.
- Live finding: the `Company` collection answers with name, GUID, books-from and starting-from dates, needs no company selected, and lists only what that Tally process has open (dev: "Fleek" on 9000, "Fleek Test" on 9001, one each).
- Built:
  - `tally_company` gains `company_guid`, `books_from`, `verify_state`, `verify_note`, `verified_at` (migration 0072, additive, all null until verified).
  - `services/tally/company_verify.py`: "Verify company" queues a read-only export through the agent's existing read-only export action, so no agent release. The answer is judged against what we stored:
    - `verified`: the open company matches; the first time, its GUID and books-from are stored.
    - `renamed`: our stored GUID is open under another name. We do not rename the link behind the user's back; the note says pushes will be refused until the name here matches.
    - `different`: a company with our name is open but its GUID is not the stored one.
    - `not_open`: nothing matching is open; the note lists what is open. Nothing stored is overwritten.
  - `POST /api/admin/firms/{id}/tally/company/verify`; the company out-schema carries the new fields. Changing the company name on purpose clears the stored GUID and state, so a deliberate relink is not flagged against the old company.
  - Web (admin Tally panel): a "Verify company" button, "Verified ... books from ..." line, the Tally id, and the warning text for the three bad states.
  - Tests: 16 new (request, parser, the four verdicts, the flow, agent failure). Tally-related backend set 278 passed, 1 skipped, 0 failed; web eslint and tsc clean.
- Not covered, deliberately: a first masters pull does not fill the GUID by itself (the masters export does not carry the company's GUID); a pull or push is not yet blocked on `different` (S0.3 makes the agent refuse); the check only sees the one Tally process the agent's gateway reaches (S1.10 covers a second process on another port).
- Still to check: migration 0072 on Postgres; the whole flow through a real agent (tests call the result callback directly).
- Touches: `models/tally_connector.py`, migration 0072, `services/tally/company_verify.py`, `services/tally/jobs.py`, `routers/tally.py`, `routers/tally_agent.py`, `schemas_tally.py`, `web/src/pages/admin/TallyPanel.tsx`, `api.ts`, `lib/types.ts`.
- Size: M.

### S0.5 A line goes to Tally in the item's own unit (BUILT 2026-10-30, uncommitted)
**As** a shop owner, **I want** a voucher line to go out in the unit Tally holds for that item, **so that** no quantity is silently lost.
- Closes: B3 (code). Depends: S0.1.
- **Design changed after the live test.** First draft was a per-company unit map plus a known-units list. The test showed the real rule is per item: `nos`/`NOS` match `Nos`, but `pcs` against a `Nos` item and `kg` both post with blank quantity and rate, even when a `Pcs` unit exists. Our unit table folds `Pcs` into `nos`, so we cannot tell them apart from our own data. So no unit map: each item records Tally's own unit name.
- Built:
  - `item.tally_unit` (migration 0070, backfilled from the staged masters row the item was committed from); filled on masters-pull commit (refreshed every pull, Tally owns it) and on Check Tally (all linked items, by Tally id).
  - `services/tally/units.py::line_unit(item, line_uom)` returns Tally's own spelling and a problem (`unknown` or `differs`). Same canonical unit gives Tally's spelling (`Pcs` stays `Pcs`).
  - Sales and purchase serializers send that spelling for linked items; the purchase push passes it per line.
  - Readiness blocks with `unit_unknown` (never seen: pull masters) and `unit_mismatch` (line in another unit: change the line), for sales and purchase, naming the items.
  - Tests: 6 helper, 3 integration (unknown holds the push, mismatch blocks, `Pcs` goes out as `Pcs`), 1 import records the unit; existing fixtures updated to record a unit as a real pull does. 105 results in the Tally-related set, 104 passed, 1 skipped, 0 failed. Full backend suite not run.
- Consequence to know: a linked item with no recorded unit is held until the next masters pull or Check Tally (the migration backfills what it can).
- Not done, deliberately: creating a missing unit at push time (new purchase items still create theirs as before); converting between units (Tally needs alternate-unit setup, so a unit change is a block, not a guess); web wording for the two new blockers (the existing blocker list shows the message text).
- Still to check: migration 0070 on Postgres (plain SQL, not run there); a live push of a `Pcs`-based item through the agent.
- Touches: `models/item.py`, `alembic/versions/0070_item_tally_unit.py`, `services/tally/units.py`, `services/tally/push_readiness.py`, `services/tally/jobs.py`, both `tally_xml.py`, `routers/items_import.py`, `services/catalog/tally_items.py`.
- Size: M.

### S0.6 Safe retry: no duplicate vouchers (BUILT 2026-10-30, uncommitted)
**As** a shop owner, **I want** a retry after a timeout never to create a second voucher, **so that** my books are not doubled.
- Closes: F1 (code). Depends: S0.1.
- Built:
  - Sales vouchers now carry our reference in the standard `REFERENCE` field: `ME/<fy>/<number>` (series added when not the default). Purchase vouchers already carried the supplier bill number there. Live-checked: Tally accepts it, a filtered export finds the voucher by it, and an absent reference returns nothing.
  - `tally_sync_job.outcome_unknown` (migration 0071) is set when a push ends without Tally answering: the agent had taken the voucher and never reported (expiry while `sent`), or the agent reported a failure. A job still `queued` at expiry is withdrawn, so it is not flagged. A Tally error (`LINEERROR`) is a definite failure, not flagged.
  - `services/tally/voucher_check.py`: a retry of a flagged document first queues a `check_voucher` job. It rides the agent's existing read-only `pull_vouchers` action, so no agent release is needed. Found: the earlier push is completed as ok and linked (real Tally GUID), nothing is posted. Not found: the flag is cleared and the retry is queued automatically (after the usual readiness check). The check failing (agent down, Tally refused the company) leaves the retry held and the flag set, with a plain message.
  - A second click while a check runs returns the same check job.
  - Web: the push panel says "checking Tally first" when the returned job is a check.
  - Tests: 15 new (reference, request, parser, the whole retry flow incl. found, not found, check failed, agent-failure flag, queued-not-flagged). Tally-related backend set 129 results, 1 skipped, 0 failed; web eslint and tsc clean. Full backend suite not run.
- Not covered, deliberately: a purchase with no bill number cannot be looked up, so its retry goes ahead as before; a voucher that someone edited in Tally to change its reference or date will not be found (the reconcile story S4.3 owns drift); duplicates already created cannot be undone from here.
- Still to check: migration 0071 on Postgres; the whole flow through a real agent (the agent action is unchanged, but the end-to-end path is only tested with the result callback called directly).
- Touches: `services/tally/voucher_check.py`, `services/tally/jobs.py`, `services/tally/push.py`, `services/invoices/tally_xml.py`, `routers/tally_agent.py`, `models/tally_connector.py`, migration 0071, `web/src/components/TallySyncStatus.tsx`.
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

### S1.10 Gateway address per company
**As** an owner whose companies are open in separate Tally windows, **I want** the agent to reach each one, **so that** pushes and pulls go to the right process.
- Closes: A5. Found live 2026-10-30: Fleek on port 9000, Fleek Test on port 9001, one company each; the agent reads one `GatewayUrl`.
- Acceptance:
  - Each company link has a gateway address (default: the agent's own). The agent reports which gateways it can reach and which company each serves (name and GUID, read from the gateway), so the owner maps them instead of typing ports.
  - Every job carries the company, and the agent sends it to the gateway that serves that company; if none serves it, the job ends with a plain "company not open" message.
  - The S0.3 company check runs against the chosen gateway.
  - Test with two fake gateways on two ports.
- Size: L. Depends: S0.3, S1.1. New agent release.

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
  - Live finding: on an auto-numbered Sales type Tally replaces our number, so our series needs a voucher type with manual numbering (created through the import path). Until it exists, store Tally's number back.
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
- Closes: C1. Depends: S0.1. Acceptance: store the key from `LASTVCHID` (the voucher master id) and derive the voucher GUID as company GUID + "-" + 8-digit hex of it; `pushed:<number>` fallback removed or flagged unreliable; also store Tally's own voucher number, which differs from ours (C3).
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
| A5 | S1.10 | | | |
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
| 2026-10-30 | S0.1 Building. Two separate Tally processes: port 9000 serves "Fleek" (GUID 167b31a2-6486-4396-8ee8-7fe38496052d, 18 vouchers, 2339 stock items, ZZTEST ledgers from earlier runs); port 9001 serves "Fleek Test" (GUID 67baeb49-5454-418e-aad2-4ae28441e6d1, empty: Cash and P&L only, no items, no units, 0 vouchers). Each gateway lists only its own company. The agent reads one `GatewayUrl`, so it can reach only one: new gap A5, new story S1.10. No vouchers posted yet. |
| 2026-10-30 | S0.1 DONE: 7 live posts into the dev companies (4 vouchers landed in Fleek, master ids 19 to 22; the 3 refused or failed posts changed nothing). Findings in the S0.1 section. B3 raised to Critical, E2 lowered to Med, F1 and C3 confirmed, C1 simplified. |
| 2026-10-30 | S0.3 BUILT (see its section): backend + agent + panel; decisions taken: retry while not open, stop on renamed/different, block push on both. Agent release still to do. |
| 2026-10-30 | Live: both companies loaded in one Tally window on 9000; Company collection lists both with GUIDs; a push by company name reaches the right books (id 28 posted to Fleek; Fleek Test refused a ledger it lacks). S0.3 can ship against ONE gateway; S1.10 (several gateways) is deferred until shops need it. Design updated. |
| 2026-10-30 | S0.3 and S1.10 merged into one agent release; DRAFT design in `docs/DESIGN-agent-company-routing.md` awaiting review (4 decisions). Proposes dropping the per-company gateway address: the agent routes by company identity. |
| 2026-10-30 | S0.4 BUILT (see its section). Live: Company collection returns name, GUID, books-from per open company. |
| 2026-10-30 | S0.6 BUILT (see its section). Live: sales `REFERENCE` accepted and found by a filtered export. Dev Fleek now also holds voucher id 27 (reference ME/2026-27/9301). |
| 2026-10-30 | S0.5 BUILT (see its section). Live unit test: `nos`/`NOS` fine; `pcs` vs a Nos item and `kg` post blank, voucher ids 23 to 26 in dev Fleek. Design moved from a unit map to a per-item Tally unit. |
| 2026-10-30 | S0.2 code done, unit and integration tests pass (60 tests across push, inward xml, connector, self-serve). Not yet confirmed live that Tally accepts `SVCURRENTCOMPANY` in a voucher Import. Adds a push blocker `company_name_missing`. Uncommitted. |

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

### S1.1 Company registry: many per firm (FOUNDATION BUILT 2026-10-30, uncommitted)
**As** an owner, **I want** to add more than one Tally company to my firm, **so that** GST and Estimate books both work.
- Closes: G2. Decisions: D-6 (field), D-7 (field).
- Acceptance:
  - Drop `uq_tally_company_tenant`. Add `kind` (`gst`, `estimate`, `branch`, `fy`, `other`), `is_default`, `series_prefix`, `document_label`, `include_in_reminders`, `visible_roles`, `status`.
  - Exactly one default per firm, enforced in the database (partial unique index) and in code.
  - Migration marks each existing company `is_default`, kind `gst` if the firm has GST on else `other`, `include_in_reminders` true, `visible_roles` null (everyone). Existing shops behave as before.
  - `get_tally_company(tenant)` is replaced by `get_default_company` and `get_company(id)`; all 15 call sites in 6 files are moved, none left on the old single lookup.
- Touches: `models/tally_connector.py`, migration, `services/tally/push_readiness.py`, `routers/tally.py`, `routers/tally_self_serve.py`, `routers/inward.py`, `services/inward/approve.py`, `services/catalog/tally_items.py`.
- Built: model drops `uq_tally_company_tenant`, gains `kind`, `is_default`, `series_prefix`, `document_label`, `include_in_reminders`, `visible_roles`, `status`; partial unique index `uq_tally_company_default` (migration 0075; existing companies become the default, kind `gst` where the firm has GST on). New `services/tally/companies.py` (`get_default_company`, `get_company` scoped to the firm, `list_companies`, `add_company`, `set_default`). The 10 product call sites of the two old lookups now use the default. The admin Tally page still manages only the default company; there is no way to add a second yet (S1.4). Tests: 9 new. Full backend run: 1443 passed, 32 failed because two test files built the company row by hand without `is_default`; fixed, and those files plus the new tests re-ran at 54 passed. The full suite was not re-run after that fix.
- Still to do for S1.1: `tools/backfill_masters_xml.py`, `tools/tally_capture.py` and about 15 test lookups still use `TallyCompany.tenant_id ==` (fine with one company; move them before a firm gets a second); migration 0075 on Postgres; `include_in_reminders`, `visible_roles`, `series_prefix` and `document_label` are stored but read by nothing yet (S1.7, EP2, EP3).
- Size: L.

### S1.2 `tally_link` per company (BUILT 2026-10-31, uncommitted)
**As** the system, **I want** one customer or item to hold a separate Tally link in each company, **so that** the same customer can exist in both books.
- Closes: B1.
- Acceptance:
  - `tally_link` gains `company_id`; unique on (company, entity_type, entity_id) and (company, entity_type, tally_guid). Backfill sets `company_id` to the firm's default company.
  - One helper reads a link for (entity, company). The 21 reads of `party.tally_guid` or `item.tally_guid` across 10 files go through it, with the legacy column as a read-only fallback during transition.
  - Imports and pushes write the link, not the legacy column. A test proves no new write to the legacy columns.
- Built: `tally_link.company_id` (migration 0076): both uniqueness rules are per company, so one customer or item holds a separate link in each of a firm's companies. Backfill: existing links go to the firm's default company; customers and items that carried a Tally id only on the record get a link there too (oldest wins a duplicated id; Postgres only). New `services/tally/links.py` is the one place that reads or writes a master's Tally id: `guid_for`, `is_linked`, `linked_ids`, `record_master_guid`, `adopt_legacy_links`, `company_for_import`. Moved onto it: push readiness (sales and purchase), `enqueue_push_*`, `pending_master_creates` (now takes the company), the stock check, both masters-import commits, the catalog cleanup check, the item list flag and the in-Tally filter. The invoice and bill voucher links are per company too. `Item.tally_linked` is a SQL-side flag for the default company (list, filter).
- Rules: the old `party.tally_guid` / `item.tally_guid` are read only, and only as a fallback for the DEFAULT company and only for a record with no link in any company. Nothing writes them while the firm has a company (a test scans the code for it). An import by file with no company yet keeps the id on the record; the firm's first company adopts it. A link never takes an id another record holds in that company.
- Tests: 11 new in `tests/test_tally_links.py`. Full backend run before the last fixes: 1472 passed, 3 failed (a query auto-correlation bug in the new flag, and one test that asserted the old column); both fixed, catalog suite re-ran 44 passed. Full backend suite after both fixes: 1486 passed, 2 skipped, 0 failed.
- Not done, on purpose: S1.3 (use the link's Tally name on push); the item's push state (`tally_status`, `tally_price`, `tally_seen_price`) and `tally_unit` still live on the item, not per company, so a second company would share them (see S1.2b); relationship readers outside the listed files (reports, ageing) do not read masters' Tally ids today. Migration 0076 on Postgres not run.
- Touches: model, migration, `routers/parties_import.py`, `routers/items_import.py`, `routers/catalog_review.py`, `services/tally/jobs.py`, `push_readiness.py`, `tally_xml.py`, `tally_items.py`, `item_filter.py`, `schemas_item.py`.
- Size: L.

### S1.2b Item Tally state per company
**As** an owner with GST and estimate books, **I want** each company to remember what it holds for an item, **so that** a price pushed to one company, or the unit one company uses, is not mistaken for the other's.
- Found while building S1.2: `item.tally_status`, `tally_price`, `tally_seen_price`, `tally_synced_at` and, from S0.5, `tally_unit` describe ONE Tally company. With two companies the second would read and overwrite the first's. Unit matters most: the same item can be `Nos` in one company and `Pcs` in the other.
- Acceptance:
  - Move these onto the item's link in each company (or a sibling table keyed by company and item); `tally_unit` first, because a wrong unit blanks a voucher line (B3).
  - Voucher unit checks (`push_readiness._unit_blockers`) and the catalog push read the document's company.
  - The Items hub shows Tally state for the default company, with a way to see another company's.
  - Backfill from the item's current values into its default company's link.
- STATUS 2026-10-31: unit part BUILT. `tally_link.tally_unit` (migration 0077, backfilled from `item.tally_unit`); `links.unit_for` / `set_unit`; `units.line_unit` now pure, `company_line_unit` reads the document's company; sales XML takes `line_units`; pull and Check Tally write the link. The old item column is a fallback for the default company only, and moves onto the first link. 3 new tests in `tests/test_tally_links.py`; full backend suite exit 0.
- STATUS 2026-10-31: push state part BUILT. Table `item_tally_state` (company, item; status, price, seen_price, pushed_at; migration 0078, backfilled to the default company). `services/tally/item_state.py` is the only writer (`set_status`, `record_sent`, `record_seen`); the catalog push, its result handler, the stalled-run expiry and Check Tally all work on the company the run was for. `Item.tally_status/price/seen_price/pushed_at` are now read-only views of the default company (for lists and filters). The catalog item push is also blocked on a `different`/`renamed` company (S0.3). 4 new tests in `tests/test_item_tally_state.py`; full backend suite: no failures.
- STILL TO DO: the Items hub showing another company's state (needs the company switcher of S1.4); migrations 0077 and 0078 not run on Postgres.
- Size: M. Depends: S1.2.

### S1.3 GUID-first push, name stored per link
**As** an owner, **I want** a renamed customer or item to keep working, **so that** a rename on either side does not break a push.
- Closes: B1. Decision: D-8.
- Acceptance:
  - The link stores the Tally-side name seen at the last pull or push (`tally_name`).
  - A push uses the link's `tally_name`, not our current name, once linked. The first push of an unlinked record uses our name and then records it.
  - When our name and the link's Tally name differ, the record shows "name differs in Tally" with a choice to keep, or to rename in Tally on next push.
  - Test: rename the party here after linking; the voucher still carries the Tally name.
- STATUS 2026-10-31: BUILT (backend). `tally_link.tally_name` (migration 0079, backfilled from the record's name); a pull refreshes it, a first link records ours. `links.tally_name_for` is what sales and purchase pushes send for party and item (sales XML takes `party_name`, `item_names`). `links.name_differs` flags a rename. Tests in `tests/test_tally_links.py` and `tests/test_invoice_tally_push.py`.
- NOT DONE: the "name differs in Tally" badge and the keep / rename-in-Tally choice (needs the party and item screens; goes with S1.4's UI). The catalog item push still names stock items by our name (it creates them, so ours is right).
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
- STATUS 2026-10-31: API BUILT, SCREENS NOT. Decided: owners manage their own companies (theme: no unnecessary centralization; only what costs us stays with Fleek). `routers/tally_companies.py` (list, add, edit, default, switch off, verify, open, relink, ledger map, pull masters; owner vs accountant per `docs/PARITY-tally-company-management.md`), tests `tests/test_owner_companies.py`. Mockups agreed in principle on the canvas files in the scratchpad (publish was blocked by a permission rule). WEB BUILT 2026-10-31 (type-checked and linted, NOT yet clicked through in a browser): `components/TallyCompaniesCard.tsx` on the firm's Connections tab (list, add, edit, make default, switch off, check it in Tally; a firm with one company sees one card and one link). NOT DONE: the Fleek-admin read proxy (rows in the parity table); ledger names, relink and pull-masters buttons in the owner card (the API exists).

### S1.5 One ledger map per company; retire `TallyLedgerConfig`
**As** a maintainer, **I want** one source for ledger names, **so that** inward and sales cannot disagree.
- Closes: B5.
- Acceptance:
  - Inward approve and push read `ledger_map` of the document's company.
  - A migration copies each firm's `tally_ledger_config` into its default company's map where the map lacks the key; conflicts are listed in the migration output, not overwritten.
  - After one release, `TallyLedgerConfig` and its 10 reads are removed.
- STATUS 2026-10-31: BUILT. `services/tally/ledger_config.py` (`ledger_config`, `save_ledger_config`) reads and writes `tally_company.ledger_map`; sales push, purchase push, manual purchase export (`inward/approve.py`) and the inward settings API all use the default company's map. Migration 0080 copies the old config into the map where a key is missing and prints disagreements. A firm with NO company still uses the legacy `tally_ledger_config` row. Tests: `tests/test_ledger_config.py`.
- NOT DONE: dropping `TallyLedgerConfig` (wait one release, and until a company is required); per-document company choice (S2.x; today the default company).
- Size: M.

### S1.6 Per-company pull, staging and in-flight guards
**As** an owner, **I want** a pull for one company never to block or overwrite another's, **so that** I can work in both.
- Closes: C5.
- Acceptance:
  - `staging_tally_party` and `staging_tally_item` carry `company_id`; the one-uncommitted-batch rule becomes per company.
  - `assert_no_pull_in_flight`, the stock-check and push guards key on company.
  - Review and commit screens say which company the batch came from.
- STATUS 2026-10-31: BUILT. `staging_tally_party/item.company_id` (migration 0081, backfilled from the pull job); staging a batch clears only that company's uncommitted rows (a file upload has none and clears only uploads); `assert_no_pull_in_flight(session, tenant, company_id)` checks that company's running pull and staged rows; `/current` takes `company_id` and returns `company_id`, `company_name`, `other_batches`; the two import pages name the company. Test in `tests/test_tally_links.py`.
- NOT DONE: the stock-check and push guards (`run_in_flight` in `tally_items.py` is still one per firm: pushing items to two companies at once is refused, which is safe); a company picker on the import pages (the newest batch shows first).
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
- STATUS 2026-10-31: BUILT. `services/tally/visibility.py`: `can_see_company`, `visible_companies`, `default_company_hidden`, `require_visible_default` (404, so it does not reveal the company). Tests: `tests/test_company_visibility.py` (the rule for each of the 6 roles, platform admin) and `tests/catalog/test_company_visibility_http.py` (owner reaches, accountant is turned away on each surface).
- LEAK CHECKLIST (every place a signed-in user reads or acts on company data; add a line per new surface):
  - catalog Tally settings GET (looks unconnected), PUT (404), check start (404), last check GET (empty): `routers/catalog_tally.py`.
  - item push preflight, push, run: `routers/item_tally.py`.
  - invoice push status and push: `routers/tally_self_serve.py`.
  - NOT YET GUARDED: `routers/tally.py` (platform-admin only, so sees all by rule); `routers/tally_agent.py` job dispatch (agent, not a user); company list and detail for users (no such endpoint until S1.4); invoice-list Tally badges and `TallyLink` reads on invoices (S2.x adds the invoice's company); Fleek-admin proxy `control_plane_firm.py` (S1.4).
- Size: M. Depends: S1.1.

### S1.8 Relink for a new financial year
**As** an owner, **I want** to move to the next year's Tally company without losing my links, **so that** year end does not break the connection.
- Closes: A2. Depends: S0.4, S1.2.
- Acceptance:
  - A guided "link next year's company" action: pick the new company, pull its masters, auto-match parties and items to existing records by name and GSTIN, show the unmatched for review, then write links for the new company.
  - The old company stays linked and, if the owner wants, active (backdated entries). Its links are untouched.
  - Test with two companies sharing most ledgers.
- STATUS 2026-10-31: CORE VERIFIED, GUIDED SCREEN NOT BUILT. Add the new year's company, pull its masters (the batch carries that company), commit: customers match the ones we hold by GSTIN, PAN or name (no duplicates), links are written in the NEW company only, and the old company's links are untouched. `company_for_import` now trusts the batch's `company_id` first. Test: `test_next_years_company_links_the_same_customers_and_leaves_the_old_links`.
- NOT DONE: the guided "link next year's company" flow (pick, pull, review unmatched, confirm), which is screen work in S1.4; the "unmatched for review" list is today the normal import review.
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
- STATUS 2026-10-31: BUILT. The importers already filled blanks only; now it is explicit and enforced. `services/tally/ownership.py` lists the fields a pull may fill (`PARTY_FILLED_FROM_TALLY`, `ITEM_FILLED_FROM_TALLY`) and the commercial fields it must never write (`PARTY_OURS`, `ITEM_OURS`); `tests/test_tally_ownership.py` reads both commit functions' source and fails if one writes a field on neither list. A differing Tally GSTIN, PAN, HSN or GST rate is kept as ours and returned in the commit as `conflicts` (party and item `CommitOut`). Test for the conflict in `tests/test_tally_party_import.py`.
- NOT DONE: showing `conflicts` on the import result screens (the API returns them; the pages ignore them for now); a per-row review flag before commit.
- Size: M. Depends: S1.2.

---

## EP2: Routing and documents (P2)

Goal: every document knows which company it belongs to, with the right number, tax block and label; masters are created in Tally only when missing.

### S2.1 Invoice carries its company
- Closes: G4. Decision: D-5.
- **As** a user, **I want** each bill tied to one company at draft, **so that** it posts to the right books.
- Acceptance: `invoice.tally_company_id` (nullable for a firm with no companies); default by the order: explicit, party default, document type, firm default; locked at finalize and shown on the invoice; no bill posts to two companies. Migration backfills finalized invoices to the default company. Uses the S1.7 filter.
- STATUS 2026-10-31: BUILT (backend). Migration 0082; `services/tally/company_routing.py` (`choose_company` validates exists, active, visible; `resolve_company` explicit > customer default > firm default; `company_for_document`). Create takes `tally_company_id`; a defaulted company follows a changed customer, a chosen one stays; finalize fills a null one and edits are blocked after (existing rule). `InvoiceOut` carries `tally_company_id` and `_name` (null when the user may not see it). Tests: `tests/test_document_company.py`.
- NOT DONE: per-document-type default (nothing stores one); showing the company on the invoice screen (S2.3 UI).
- Size: M. Depends: S1.1.

### S2.2 Party default company
- Closes: G4. **As** an owner, **I want** to set which company a customer's bills go to by default.
- Acceptance: `party.default_company_id`, editable on the party, validated to a company the editing user can see, cleared if the company is disabled.
- STATUS 2026-10-31: BUILT (API). `PATCH /api/parties/{id}` takes `default_company_id` (validated through `choose_company`; null clears); `PartyOut` returns it. A disabled company is skipped at resolve time rather than cleared. NOT DONE: the field on the party screen.
- Size: S.

### S2.3 Company switch in the invoice editor
- Closes: G4. **As** a counter user, **I want** to pick the company on a bill when I need to.
- Acceptance: switch appears only when the firm has more than one company visible to the user; shows only visible companies; changing company on a draft re-runs defaults for series and label; mockup first.
- Size: M.
- STATUS 2026-10-31: WEB BUILT (type-checked and linted, NOT yet clicked through in a browser). `InvoiceEditorPage.tsx`: a "Posts to" select under the customer, shown only when the firm has 2+ active companies the user can see (`/api/tally/companies` already filters by role); sent only once the user picks one, so a defaulted company keeps following the customer; a final bill shows its company and the S2.10 guidance. Number series shown beside each company. NOT DONE: re-showing the number-to-be when the company changes (it still says "auto on finalize").

### S2.4 Series and label per company
- Closes: G5, C3. Decision: D-7.
- Acceptance:
  - Number sequence, prefix and document label move from the tenant to the company. Existing firms get their current sequence copied to the default company so numbers continue unbroken.
  - Live finding: on an auto-numbered Sales type Tally replaces our number, so our series needs a voucher type with manual numbering (created through the import path). Until it exists, store Tally's number back.
  - Our sales use our own series (own voucher type or prefix) in that company; the voucher type is created in Tally through the import path if missing.
  - Onboarding check confirms the target voucher type accepts manual numbers and tells the shop what to switch if not.
  - Imported Tally vouchers never touch our sequence.
- STATUS 2026-10-31: BUILT except the Tally voucher type. `companies.series_map` / `series_for`: a company's own `series_prefix` wins; the DEFAULT company without one keeps `Sales` (existing numbers carry on unbroken); any other company gets a name from its kind (EST, BR, FY, GST, CO), unique within the firm, so two companies never share a sequence. Create, slip-draft, company change and finalize set the series from the bill's company while it is a draft; duplicate keeps the source's. The PDF title uses the company's `document_label`, else the firm's. Tests: `test_series_names_are_unique_and_the_default_keeps_sales`, `test_each_company_numbers_its_own_bills` in `tests/test_document_company.py`.
- NOT DONE: creating a manually-numbered voucher type in Tally and the onboarding check that the target type accepts manual numbers (agent and Tally work; the Reference field still carries our number, so retries stay safe); storing Tally's number back; the series prefix is not yet editable (S1.4 screen).
- Size: L. Depends: S0.1, S2.1.

### S2.5 Push uses the document's company
- Closes: G4. **As** the system, **I want** every push to go to the document's own company.
- Acceptance: readiness, enqueue, job and link use the invoice's or bill's company; ledger map, units and series are that company's; sales and purchase.
- STATUS 2026-10-31: BUILT except series. Readiness (`push_blockers`, `purchase_push_blockers`), the operator and self-serve push routes, the inward push route and the approve-time auto push use `company_for_document`; a disabled company blocks (`company_disabled`); ledger map and units were already per company. Series waits on S2.4. Test: `test_a_push_goes_to_the_documents_company_not_the_default`.
- Size: M. Depends: S2.1, S1.5.

### S2.6 Inward bill carries its company
- Closes: D1. Decision: D-11 (GSTIN routing comes with EP3, S3.9).
- Acceptance: `inward_bill.tally_company_id`, default by firm default or user choice; visible filter applied; purchase push uses it.
- STATUS 2026-10-31: BUILT except user choice. Column in 0082 (approved bills backfilled to the default); approve sets it to the firm default when null; push, readiness, ledger config use `company_for_document`. NOT DONE: a way for the user to pick the company on a bill (needs the S2.3-style switch).
- Size: M.

### S2.7 Check Tally before creating a supplier or item
- Closes: D1, B2 (part).
- Acceptance: the purchase push first compares supplier and item names and GUIDs against the company's known masters (same check-Tally step the catalog push uses); creates only what is missing; a near-match is flagged for the user instead of duplicated.
- STATUS 2026-10-31: BUILT for names. `services/tally/master_check.py` (`classify`: exact, near at 0.88 similarity, new) compares a supplier against the company's last-pulled ledgers and an item against its last stock check. `pending_master_creates` leaves out an exact match (a Create would alter it) and reports a near match; `purchase_push_blockers` holds the push with `similar_master_in_tally` naming both spellings. Tests: `tests/test_master_check.py`, two in `tests/inward/test_inward_tally_push_integration.py`.
- NOT DONE: comparing GUIDs (we hold none for an unlinked record); a "this is the same one" button that writes the link (UI, then `record_master_guid` with the Tally guid from the ledger list); `known_ledgers` has no GUIDs today.
- Size: L.

### S2.8 Orders follow the party's company
- Closes: D2. Acceptance: an order turned into an invoice takes the party default company; customer-facing pages and WhatsApp show the firm name only, never the company.
- STATUS 2026-10-31: DONE by composition: `make_invoice` goes through `create_invoice`, which resolves the customer's company. Customer pages and WhatsApp read no company. Test: `test_an_order_invoice_goes_to_the_customers_tally_company`.
- Size: S. Depends: S2.1, S2.2.

### S2.9 Party roles as a set
- Closes: B4. Acceptance: a party may be both customer and supplier; group scoping (`debtors_parent`, `creditors_parent`) is per company; resolve-party ladder order is not changed.
- Size: M.
- STATUS 2026-10-31: MOSTLY ALREADY TRUE, NOT FULLY BUILT. A party is one record with a role of customer, supplier or both; an import widens the role and never narrows it (`test_gstin_match_widens_role_to_both`), and the same party is linked in each company it exists in (S1.2, S1.8 test). Resolve-party ladder untouched. NOT DONE: group scoping by `debtors_parent` / `creditors_parent` per company: nothing reads those two map keys today (the importer classifies by Tally's standard primary groups), so a custom group under Sundry Creditors is classified by its top group only.

### S2.10 Wrong-company correction (interim)
- Closes: D-5 (guidance). Acceptance: finalized invoice in the wrong company shows plain guidance (credit note plus fresh invoice; until EP4 S4.5, fix it in Tally). The full flow is S4.5.
- STATUS 2026-10-31: BUILT (API): `InvoiceOut.company_guidance` on a final bill in a firm with 2+ companies. NOT DONE: showing it on the invoice screen.
- Size: S.

### S2.11 Combined reminders with a per-company switch
- Closes: D-6. Depends: S2.1, S1.7.
- Acceptance: a reminder totals the party's overdue bills across companies marked `include_in_reminders` and visible to the approver; owner-only companies excluded by default; itemised by invoice number; the approve screen lists which companies each reminder covers; sent from the account's number with the firm name.
- STATUS 2026-10-31: BUILT (backend). One reminder per party already covered every bill; now `reminders.excluded_company_ids` leaves out companies with `include_in_reminders` off or hidden from the accountant (an owner-only book is never chased from the shared queue), and a bill with no company counts under the default. `ReminderOut` returns `companies` (when more than one) and `invoice_labels` (`EST/5`, so equal numbers in two series stay apart). Tests in `tests/test_reminders.py`.
- NOT DONE: the approve screen showing `companies` and `invoice_labels`; "sent from the account's number with the firm name" (account = S3.x); the statement PDF still lists one party ledger from our own invoices (it already spans companies).
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
- STATUS 2026-10-31: BUILT for the key. `push.voucher_identity` reads `LASTVCHID` from the Import response and stores the voucher's real GUID (`voucher_guid`: company GUID + '-' + 8 hex digits of the master id; live example: id 28 -> `...-0000001c`) as the link's `tally_guid`, and the master id in `tally_masterid`. Without the company's GUID it stores the master id; with neither, the `pushed:<number>` placeholder, which `is_confirmed_key` reports as unconfirmed. Tests: `tests/test_voucher_key.py`. NOT DONE: Tally's own voucher number (C3; the Import response does not return it, so it needs a read by Reference, which S4.3 will do); using `is_confirmed_key` in the UI or in reconcile.

### S4.2 Voucher import into native rows
- Closes: C2. Decision: D-8. Follows `PLAN-tally-voucher-import-and-party-prices.md`, per company, whole not in pieces.
- Split: S4.2a parser and fixtures; S4.2b apply sales as native invoices (`source=tally_import`); S4.2c apply receipts with bill allocations; S4.2d opening balances and bills receivable; S4.2e visibility filter applied to imports.
- Size: XL (split above).
- STATUS 2026-10-31: REPLACED for now by the refill (user: past invoices are not needed yet). S4.2a parser BUILT (`services/tally/voucher_xml.py`, 11 tests). S4.2d BUILT in a lighter form: Bills Receivable sets `party.opening_balance` where the party has no history, plus a "Tally says" snapshot (`refill.apply_outstanding`). S4.2b/c/e (native invoices and payments from Tally) NOT BUILT and not planned until collections need them.

### S4.3 Reconcile and flags
- Closes: C1. Decision: D-8. Acceptance: for each voucher we pushed, flag "missing in Tally" and "changed in Tally"; never auto-delete or overwrite; a daily or on-demand run.
- Size: L. Depends: S4.1, S4.2a.

### S4.4 Receipt push
- Closes: C2. Decision: D-7. Acceptance: a recorded payment pushes as a Receipt voucher in our series with bill-wise allocation; ledger from the payment mode and the company's map.
- Size: L.
- STATUS 2026-10-31: BUILT (backend), VERIFIED LIVE. Posted two Receipt vouchers to the dev company "Fleek" (ids 29 and 30): a party-credit / cash-debit Receipt with an "Agst Ref" `BILLALLOCATIONS.LIST` named by our invoice number reduced that bill in Tally (9104: 100.00 -> 60.00); an unapplied amount as a "New Ref" under our own receipt reference posts too. `services/tally/receipts.py`: `split_by_company` (a payment is pushed once per company: each invoice's part to its own company, on-account to the default), `receipt_ledger` (cash or bank ledger from the payment mode and the company's map, D-7), `receipt_blockers`, `build_receipt_envelope`, `enqueue_push_receipt`, checksum so an unchanged re-push is a no-op. Result handling is the shared `process_push_result` (link entity `payment`, real voucher GUID, S4.1). Routes: `GET /api/tally/payments/{id}/push-status`, `POST /api/tally/payments/{id}/push` (one job per company, hidden companies skipped). The agent runs it under its existing `push_sales` action name, so no agent release. Tests: `tests/test_receipt_push.py`.
- NOT DONE: the unknown-outcome check for receipts (S0.6 covers sales and purchase only; a receipt retry after "agent never answered" could post twice, so `outcome_unknown` is not set for receipts yet and the stuck-job message is the generic one); a "Send to Tally" button on the payment screen; reversing a pushed payment in Tally (a reversed payment is refused with a message); bill names assume one series per company (an invoice number repeated in two series of the same company would collide).


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

- STATUS 2026-10-31: BUILT and verified live. Stock comes from a light StockItem query keyed by Tally's id (`light_masters.parse_stock_items`, request `voucher_capture.build_stock_items_request`; 2,339 items = 1.4 MB on the dev Tally), stored per company on `item_tally_state.stock_*` (migration 0084) by `refill.apply_stock`. A Stock Summary report request was tried and dropped: asked via the Gateway it returns one rolled-up row. `stock_summary.py` stays as a parser for a UI export (matched by name). The agent that runs it (`pull_vouchers`) is not in the released 1.0.2.

### S6.2 Show stock with its age
- Closes: E2. Acceptance: item and editor show stock "as of" a time, per company; stale or unknown is shown as such; billing never blocks.
- Size: M.
- STATUS 2026-10-31: PARTLY. Items list shows quantity and unit of the default company, with the as-of date on hover. NOT DONE: other companies' stock, staleness wording, the invoice editor.

### S6.3 Negative-stock warning
- Closes: E2. Acceptance: warn only; switchable off per shop; the explained error when Tally itself rejects a push.
- Size: S.
- STATUS 2026-10-31: PARTLY. The Items list shows Tally stock in red when it is below zero (`ItemsPage`); counted per refresh (`StockResult.negative`). NOT DONE: the warning in the invoice editor, the per-shop switch, and the explained push error.

### S6.4 One creator for Tally stock items
- Closes: D4, B2. Acceptance: written rule for `SupplierCatalogItem`, `CatalogProduct` and `Item`; one code path creates Tally stock items; duplicates by name or part number blocked.
- Size: M.

### S6.5 Catalog "In stock" from stock
- Closes: E3. Status: Parked until you ask. Acceptance when taken: reads S6.1 numbers per company.

### S6.6 Price points per company
- Closes: D3. Acceptance: `supplier_price_point` carries a company where it matters; no change for one company.
- Size: S.
- STATUS 2026-10-31: BUILT. `supplier_price_point.tally_company_id` (migration 0083, backfilled from the bill); approve now fixes the bill's company first and the bill's prices carry it; a quote carries none (a price list belongs to no company). Test: `test_an_approved_bills_prices_remember_its_company`. NOT DONE: filtering price history by company in the screens (one company today).

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
| 2026-10-31 | S1.2 BUILT (see its section). New story S1.2b: item Tally state and unit are per company, not per item. |
| 2026-10-30 | S1.1 foundation BUILT (see its section). |
| 2026-10-30 | S0.3 BUILT (see its section): backend + agent + panel; decisions taken: retry while not open, stop on renamed/different, block push on both. Agent release still to do. |
| 2026-10-30 | Live: both companies loaded in one Tally window on 9000; Company collection lists both with GUIDs; a push by company name reaches the right books (id 28 posted to Fleek; Fleek Test refused a ledger it lacks). S0.3 can ship against ONE gateway; S1.10 (several gateways) is deferred until shops need it. Design updated. |
| 2026-10-30 | S0.3 and S1.10 merged into one agent release; DRAFT design in `docs/DESIGN-agent-company-routing.md` awaiting review (4 decisions). Proposes dropping the per-company gateway address: the agent routes by company identity. |
| 2026-10-30 | S0.4 BUILT (see its section). Live: Company collection returns name, GUID, books-from per open company. |
| 2026-10-30 | S0.6 BUILT (see its section). Live: sales `REFERENCE` accepted and found by a filtered export. Dev Fleek now also holds voucher id 27 (reference ME/2026-27/9301). |
| 2026-10-30 | S0.5 BUILT (see its section). Live unit test: `nos`/`NOS` fine; `pcs` vs a Nos item and `kg` post blank, voucher ids 23 to 26 in dev Fleek. Design moved from a unit map to a per-item Tally unit. |
| 2026-10-30 | S0.2 code done, unit and integration tests pass (60 tests across push, inward xml, connector, self-serve). Not yet confirmed live that Tally accepts `SVCURRENTCOMPANY` in a voucher Import. Adds a push blocker `company_name_missing`. Uncommitted. |

## Refill of parties and items (2026-10-31, replaces the full voucher import for now)

Not a story in the epics above; it is what S4.2d, S6.1 and the party price history became. Plan: `PLAN-tally-voucher-import-and-party-prices.md` (direction change section).

One read model, one chain, keyed by Tally's own id (the id the masters pull already stored on `tally_link`), so nothing is matched by spelling where Tally can give an id:

- `light_masters.py`: ledger query (id, group, closing and opening balance) and stock-item query (id, group, unit, closing quantity, value, rate). Verified live; about 500 bytes a row against a 78 MB masters file.
- `refill.py`: `_Matcher` (id first, name only for rows with no id), `apply_stock`, `apply_outstanding` (sources: ledger closing balance or pending bills; customers with no ledger get `opening_balance`, suppliers and parties with history get the snapshot only, a different hand-typed figure is kept and reported), `apply_sales` (last sale and last rate per party and item). A rename in Tally corrects the link's stored name on its own.
- `refill_job.py`: items, ledgers, then sales a month at a time (newest first), one capture job at a time; a failed step ends the chain, earlier steps stay.
- Routes `POST /api/tally/companies/{id}/refresh`, `GET .../refill`; the company card shows progress, counts, "N in Tally are not in your list yet" and "N renamed in Tally, updated". Price lookup merges Tally's last rate (source "tally", visible companies only); party form and Items list show the figures.
- Live check 2026-10-31 on the dev Tally: ledgers 12 rows, stock items 2,339, sales vouchers Sep-2026 17 sales + 2 receipts + 10 purchases + 1 journal parsed.
- Group lineage: Positive's customers sit under groups of their own (AASAM, SILIGURI, JAIGOUN ...), only 50 of 1,412 directly under Sundry Debtors. The chain reads the Group list first (`light_masters.parse_groups`, 28 groups on the dev Tally) and walks it with the masters import's own `tools/tally_import/groups.py` `GroupTree`, carried down the chain on the job. Closes the S2.9 gap for the refill.
- Incremental (2026-10-31, verified live): every ledger and stock item carries an `AlterID`. A query with `$AlterID > N` returns only what changed (dev Tally: ledgers above 217 gave 5 of 12, stock items above 1540 gave 1,169 of 2,339). `tally_company.ledgers_alter_id / items_alter_id` hold the highest seen (only ever move up, only on rows Tally sent); `full_refill_at` stamps the last complete read. `refill_job.choose_mode`: full the first time and at least weekly (a deleted record is not a change), otherwise changes only, with sales for this and last month. Opt-in daily refresh: `tally_company.auto_refresh` (owner's company form), `refill_job.run_auto_refresh` from `main._refill_loop`, hourly, 08:00 to 20:00 India time, agent online, none within 20 hours (a failed or running chain is not retried until the next day).
- Refill gaps closed (2026-10-31): (1) deleted in Tally: a full read lists linked items and parties that are gone, clears an item's stock and a party's Tally balance (never the opening balance), and leaves everything alone when more than half the list vanishes at once (a failed read); (2) pending-bill dates: a full read adds a `bills` step (Bills Receivable) giving each party's open-bill count and oldest date, counting parties whose bills do not add up to the ledger; (3) typed-vs-Tally opening balances: the owner or accountant reviews each difference on the company card and takes Tally's figure or keeps theirs (`party_tally_snapshot.kept_ours_outstanding`, hidden until Tally's figure moves; locked parties refused); (4) units: all Tally spellings seen at Positive (Pcs, NOS, Doz, Kgs, Set, BOX, GROSS, Nug) resolve through the canonical unit table and the editor already shows a different-unit Tally price without filling it. Tests: `test_refill_gaps.py`.
- DECIDED NOT TO BUILD: incremental sales by AlterID (the last two months re-read daily and three months weekly already catch a late bill); rebuilding the first masters import on the light queries (the masters pull works; Positive's real ledger fields `LEDGERMOBILE` and `PRIORSTATENAME` could only be probed on a dev ledger with data).
- NOT DONE: the agent release that carries `pull_vouchers`; a first run on the pilot (size and time of a real month of sales); Bills Payable detail for suppliers.

## Fleek admin: read-only company view (2026-10-31)

BUILT in Metal ERP: three signed read-only routes (`GET /tally-companies`, `.../{id}/refill`, `.../{id}/jobs`) in `control_plane_firm.py`, sharing the owner routes' functions; tests in `test_control_plane_companies.py`. NOT BUILT: the Fleek admin screen in fleek-frontend and the fleek-backend proxy that signs and forwards these calls (Fleek parity table first, per the standing rule).

## Two open copies of one company (found at the pilot, 2026-10-31)

Positive's agent reported two open companies with the SAME Tally id (`b8c7a8b8-...-0357f`) and the same books-from date: "ESTIMATE & ORDER NEW" and "ESTIMATE & ORDER NEW 1". That is a copy (a restored backup or copied data folder opened beside the original), not a rename: a company made new gets a new id. The verify check (`company_verify.judge`) took the first open company with the stored id, saw a different name and said "renamed", which blocked pushes (`push_readiness._wrong_company_blockers`) although ours was open under its own name. Fixed: a new verdict `duplicate` (our name is open: nothing is blocked, a warning shows on the card), `company_payload` leaves the id out of jobs in that state so the agent checks by name (agent 1.0.3 also stops a job as "renamed" on the first id match), and the follow-a-rename change (`adopt_rename`) only acts when exactly ONE open company carries our id, so it can never move pushes onto the other copy. Danger of two copies: voucher keys are the company id plus the voucher's master id, so entries made in both copies collide in `tally_link`. NOT DONE: the agent's own `CompanyCheck.Decide` still picks the first id match; a release should prefer an exact name among copies. Tests: `test_company_rename.py`.

## Last rates from a Tally file (2026-10-31, after the sales read timed out at the pilot)

At Positive the gateway sales read timed out even for ONE day (the agent waits ~105 s; Tally answers the item, ledger and bill reads in 0.4 to 2 s), so the window tuning cannot help and the cause is the voucher request itself on that Tally. MAX_TIMEOUTS is now 2 so a failing read stops after two abandoned requests (Tally does not cancel a request the agent gave up on). Fallback built: `POST /api/tally/companies/{id}/refill/sales-file` takes a voucher file exported from Tally (UTF-16 or UTF-8, hundreds of MB, streamed by `voucher_xml.iter_vouchers`, never stored), classifies custom types by the parents the last refresh read, and sets last rates and last sales through the same `apply_sales`. Card: "Fill last rates from a Tally file" (opens by itself while there are no rates). Verified: the Gateway's "Voucher Register" report returns the same VOUCHER shape a file export has and parses (30 vouchers, 17 sales on the dev Tally); NOT verified: a real UI export (Day Book, Alt+E, XML) from Positive: ask the shop to try one day first. NOT DONE: the diagnosis ladder on Positive's live Tally (voucher ids only, then header, then item lines, then ledger lines) to find why the gateway request is slow, which needs the shop's agreement. Tests: `test_sales_file.py`.


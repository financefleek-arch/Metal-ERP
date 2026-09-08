# EXECUTION PLAN — F1a: Tally Connector, masters-in (Gateway export)

Status: **backend + agent + Ops UI + firm-scoped agent provisioning BUILT
& tested (422 pass, 2 skip; web tsc/lint/build clean; agent dotnet build
clean). Infra wiring + a live end-to-end run are the only gaps.**
(2026-09-08). First slice of **F1** (`MASTER-tally-complement-saas.md` §F1).
NOT committed / NOT deployed — user does check-ins.

### Agent identity — provisioned per firm, from the console (2026-09-08)
The CLI `tools.make_backup_shop` is no longer the onboarding path. A firm's
companion-agent identity is created from its own Ops-console page and every
shop-side capability (Tally sync now, backup/health/voucher-push later)
keys off that one row.

- **Migration `0024`** — partial unique index on `backup_shop.tenant_id`
  (`WHERE tenant_id IS NOT NULL`): one agent per firm; legacy CLI rows with
  a null tenant_id are untouched. `BackupShop` model + `__table_args__`
  updated; the "informational soft link" comment is now "the real link".
- **`POST /api/admin/firms/{id}/tally-shop`** — creates the `BackupShop`
  (`name = firm.legal_name`, `tenant_id = firm_id`), returns
  `{shop_id, api_key, created}` — the plaintext key **once**, never again
  (same contract as a user password). 409 if one exists.
- **`POST .../tally-shop/rotate-key`** — new key, old one dead. 404 if no
  agent.
- **`GET .../tally-shop`** — `{provisioned, shop_id, is_active,
  last_checkin_at, last_upload_at}`, no key. (Schemas `FirmTallyShopOut` /
  `FirmTallyShopProvisionResult` were already scaffolded in `fe6fc32`;
  wired now.)
- **`POST /api/admin/firms/{id}/tally/company`** no longer takes `shop_id`
  in the body — it's resolved from the firm's provisioned agent
  (`BackupShop.tenant_id == firm_id`). `pull-masters` still 422s if that
  agent doesn't exist yet.
- **`TallyPanel`** now renders **two** stacked sections: **Shop agent**
  (provision / rotate-key / reachability pill / copy-once key box) and
  **Tally** (disabled until the agent exists). No shop picker.
- Tests: `test_tally_agent.py` +3 (provision returns key once + 409 on
  re-provision + key works for auth; rotate invalidates old; rotate-without
  -agent 404). `test_tally_connector.py` reworked to provision via the
  endpoint instead of a bare CLI shop.
Delivers the read-only half of the keystone: a registered Tally company, its
ledger map, and an on-demand "pull masters" that lands parties + items +
product groups + opening balances into the existing import review screen.
No voucher push (that's F1b).

**Decisions locked 2026-09-08:**
1. **Transport = Tally HTTP Gateway, folder-read as fallback.** Verified
   live on a standard TallyPrime running as Server on port 9000: the
   `Export / List of Accounts / All Masters` request returns 625 KB of
   masters XML with `STATUS 1`, and the existing `parse_masters()` handles
   it unchanged (28 groups, 3 ledgers, GUIDs + GSTIN extracted;
   `SUGAL FOODS` under Sundry Creditors → supplier, `Cash` / `P&L` → not a
   party, exactly as designed). So the agent asks Tally directly — **no
   accountant "click Export" step, no stale file.** `TallyExportDir` stays
   as an optional fallback for a shop that won't enable "acts as Server".
   Two agent states are "not ready, retry" not errors: **no company loaded**
   (`LINEERROR: Could not find Company ''`) and **connection refused**
   (Tally closed).
2. **R2 = reuse `r2/shared` Vault creds + a hard-coded bucket
   `metalerp-tally`.** `config.py` already has the four `tally_r2_*`
   settings; the infra diff is ~6 lines (see "Migration / deploy"). This
   also finishes the "R2 bucket still open" leftover from the F10 slice.
3. **Scope = parties + stock items only** (product groups fall out of the
   item import as before). Every other `<LEDGER>` (Sales, Purchase,
   CGST/SGST/IGST, Round Off, Bank, Cash) is captured **as a name only** in
   `tally_company.known_ledgers` for the F1b voucher-out ledger-map — no
   Metal-ERP record is created for it. Groups are read for debtor/creditor
   lineage and their names go into `known_ledgers`; the group tree itself
   is not stored. Currencies / cost centres / godowns / voucher types are
   ignored. Vouchers are not in a masters export and are out of scope for
   all of F1a–F1c.
4. **Tenancy = shared tables + `tenant_id`, unchanged.** Same row-level
   multi-tenant model as the rest of the app; the staging tables and
   `tally_*` tables all carry `tenant_id` and the in-flight guard is
   per-tenant. Schema-per-firm was considered and deferred — it is a
   whole-app platform initiative (multi-schema Alembic, `search_path` per
   session), not an F1a concern.

No separate `enumerate-ledgers` action — `known_ledgers` is derived from the
masters pull itself.

## Why this slice first

- **Lowest risk half of F1** — read-only. Nothing is written back to the
  customer's Tally, so a bug can't corrupt their books.
- **~60% already exists.** `api/tools/tally_import/parser.py` already parses
  Tally masters XML into dataclasses (LEDGER→party, STOCKITEM→item,
  GROUP/STOCKGROUP with PARENT nesting, UTF-16 BOM, nested addresses, nested
  vs flat HSN/GST). `party.tally_guid` + `item.tally_guid` columns exist.
  The existing `/api/parties/import` + `/api/items/import` staging flows
  prove the mapping end to end against real files (Sugal Foods, the Tally
  party-import hardening arc).
- **`AgentOutboxItem` + `POST /api/tally-agent/checkin`** already give us a
  per-shop job queue and a drain point — F1a rides it instead of inventing a
  transport.
- Forces the **ledger-map** decision (the master doc's #2 open question) and
  the **pilot Tally audit** now, before F1b needs them.

## End-to-end flow (F1a scope)

```
 Operator clicks "Pull masters"           (Ops console)
        │  creates a tally_sync_job + a paired AgentOutboxItem
        │  {module:"tally", payload:{job_id, action:"pull_masters", company_name}}
        ▼
 tally-agent checkin picks it up          (existing checkin/outbox path;
        │                                  checkin now flips a `tally` item to `sent`
        │                                  and the job queued -> sent)
        ▼
 TallyMastersModule.RunOnceAsync:
        │  POST the All-Masters export envelope to http://localhost:9000
        │    STATUS 1                     -> got the XML
        │    "Could not find Company"     -> status no_company_loaded, leave job queued
        │    connection refused / timeout -> status tally_unavailable, leave job queued
        │    (optional) TallyExportDir set -> read newest *.xml there instead
        ▼
 agent uploads the XML to R2              (existing upload-request / presigned-PUT path)
        │  POST /api/tally-agent/jobs/{id}/result  {status:"ok", r2_key}   ← NEW endpoint
        ▼
 backend downloads the XML from R2, parses, stages a batch, derives known_ledgers,
 marks the job ok + counts                (BUILT — process_pull_result)
        ▼
 Operator reviews the batch in the existing ImportPage, clicks "Commit"
        │  → parties + stock items + product groups created or blank-filled
        │  → tally_link rows written (F1b will also read/write these)
```

The masters export envelope (verified live):

```xml
<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>
 <TYPE>Data</TYPE><ID>List of Accounts</ID></HEADER>
 <BODY><DESC><STATICVARIABLES>
  <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  <SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY>   <!-- omit to use the open company -->
  <ACCOUNTTYPE>All Masters</ACCOUNTTYPE>
 </STATICVARIABLES></DESC></BODY>
</ENVELOPE>
```

---

## Data model (new) — migration `0022`

Verify `alembic heads` == `0021` first; `down_revision = "0021"`.

```
tally_company
  id            uuid pk
  tenant_id     fk tenant.id, UNIQUE  (one linked Tally company per tenant for M1)
  company_name  str(200)              -- as it appears in Tally ("List of Companies")
  base_currency str(3)  default 'INR'
  transport     str(8)  default 'file'  -- 'file' | 'gateway' (gateway = F1c)
  shop_id       fk backup_shop.id NULL  -- which companion-agent install serves this company
  ledger_map    JSON NOT NULL default '{}'
                -- {sales_ledger, cash_ledger, bank_ledger, round_off_ledger,
                --  cgst_ledger, sgst_ledger, igst_ledger,
                --  debtors_parent, creditors_parent}
                -- values are Tally ledger/group NAMES, operator-picked from an
                -- enumerated first pull. Unused by F1a except debtors_parent
                -- (scopes which ledgers count as parties) — the rest is F1b.
  known_ledgers JSON NULL             -- last enumerated [{name, parent}] for the map dropdowns
  last_masters_pull_at  datetime NULL
  created_at / updated_at

tally_link
  id            uuid pk
  tenant_id     fk tenant.id, index
  entity_type   str(10)   -- 'party' | 'item' | 'group'   (F1b adds 'invoice' | 'payment')
  entity_id     str(36)   index
  tally_guid    str(64)   index
  tally_masterid str(32) NULL
  last_pulled_at datetime NULL
  last_pushed_at datetime NULL      -- F1b
  checksum      str(64) NULL        -- hash of the pulled fields, to skip no-op re-applies
  UNIQUE(tenant_id, entity_type, entity_id)
  UNIQUE(tenant_id, entity_type, tally_guid)

tally_sync_job
  id            uuid pk
  tenant_id     fk tenant.id, index
  company_id    fk tally_company.id
  direction     str(3)    -- 'in' (F1a) | 'out' (F1b)
  kind          str(20)   -- 'pull_masters'  (F1b: 'push_sales', 'push_receipt')
  status        str(10)   -- 'queued' | 'sent' | 'running' | 'ok' | 'error'
  outbox_item_id fk agent_outbox_item.id NULL  -- the queue row the agent drains
  r2_key        str(500) NULL     -- the masters XML the agent uploaded
  batch_id      str(36) NULL      -- the staging batch this pull produced
  counts        JSON NULL         -- {ledgers, items, groups, matched, created, filled}
  error         text NULL
  created_at / completed_at
```

**Note on `tally_guid` already on `party`/`item`:** keep it — `tally_link`
is the forward-looking generic index (and carries `checksum` + push
timestamps F1b needs), but the existing importer writes `party.tally_guid`
and F1a's upsert keys on it first. F1a **backfills `tally_link` from those
columns** on first pull so the two don't diverge. Do not drop the columns.

---

## Backend changes — BUILT 2026-09-08 (398 tests pass, +7 new)

### 1. Models + migration — DONE
- `api/app/models/tally_connector.py` — `TallyCompany`, `TallyLink`,
  `TallySyncJob`. Exported from `models/__init__.py`.
- `api/alembic/versions/0022_tally_connector.py` — creates the three tables
  + indexes, adds nullable `sync_job_id` to `staging_tally_party` /
  `staging_tally_item`. `_JSON = JSON().with_variant(JSONB, "postgresql")`.
  Verified: `create_all` builds all columns; downgrade roundtrips. (The
  `alembic upgrade` chain still can't run end-to-end on SQLite because of
  the pre-existing `0002` `CREATE EXTENSION pg_trgm` — real migration test
  is a Postgres deploy.)

### 2. Parser + shared staging service — DONE
- **`tools/tally_import/parser.py`** — added `TallyLedger.opening_balance`
  (float | None) + `_opening_balance(led)` helper: reads
  `LEDGER/OPENINGBALANCE`, normalises the sign (Cr / `ISDEEMEDPOSITIVE=No`
  → negative; Dr / positive → the party owes us). Verified against
  hand-built and live XML.
- **`tools/tally_import/parser.py` NOT moved** — the plan floated relocating
  it under `app/services/`; instead the new service imports from `tools.*`
  as the routers already do (5 call sites, moving it was more churn than the
  slice needs). Revisit if `tools/` import from a request path becomes a
  real problem.
- **`app/services/tally/staging.py`** — `stage_masters_xml()` /
  `stage_stock_items_xml()`, the parse→match→stage core **extracted
  verbatim** from the two upload handlers' bodies. Both the manual file
  upload routes AND the connector pull now call these, so staging is
  identical regardless of source. `sync_job_id` (nullable) is the only new
  per-row field; the review/commit endpoints are unchanged and
  source-unaware. Returns a summary dataclass (party: `groups` +
  `known_ledgers` = every LEDGER/GROUP name+parent; item: `dummies_skipped`).
- `routers/parties_import.py` / `routers/items_import.py` `upload` handlers
  are now thin wrappers over the service (dead helpers `_clip` / `_map_uom`
  / `_proposed_type` removed from items_import). All 37 pre-existing
  Tally-import tests still pass — the refactor is transparent.

**No `apply_masters` / blank-fill / match-ladder service** — that logic
already lives in the existing `commit` endpoints on the two import routers.
F1a stages; the operator reviews and commits through the unchanged flow.
`tally_link` rows are written by F1b (voucher-out reads them for
idempotency); F1a's commit path does not populate them yet — noted as an
F1a follow-up if the review UI wants to show "already linked to Tally".

### 3. Job lifecycle + in-flight guard — DONE
- **`app/services/tally/jobs.py`**:
  - `assert_no_pull_in_flight(session, tenant_id)` — 409 if a `pull_masters`
    job is non-terminal (`queued|sent|running`) OR terminal-`ok` with
    connector staging rows still un-committed (`sync_job_id IS NOT NULL AND
    committed_as IS NULL`). Called by `pull-masters` **and** both file-upload
    routes.
  - `enqueue_pull_masters(session, company)` — 422 if `company.shop_id` is
    null; else creates the `TallySyncJob` + paired `AgentOutboxItem`
    `{module:"tally", payload:{job_id, action:"pull_masters"}}`.
  - `mark_job_sent` / `complete_job_ok` / `complete_job_error`.
- **`checkin` (`routers/tally_agent.py`)** now flips `module=="tally"` outbox
  items to `sent` after handing them over (dispatch-once) and advances the
  paired job `queued -> sent`. Other modules' items keep their prior
  behaviour (they have no drain-confirm yet).

### 4. Pull processing — DONE
- **`app/services/tally/pull.py`** — `process_pull_result(session, job, *,
  ok, r2_key, agent_error)`:
  - `ok=False` → job `error` with the agent's message.
  - no `r2_key` → job `error`.
  - else: job `running`, `get_object(r2_key)` from R2, `stage_masters_xml`
    (a stock-only export → 0 ledgers is fine) + `stage_stock_items_xml`
    (parties-only → 0 items is fine), one shared `batch_id`. Both empty →
    job `error`. Derive `company.known_ledgers`, set
    `company.last_masters_pull_at`, job `ok` + counts
    `{ledgers, items, dummies_skipped}`.
  - Idempotent: a second call on a terminal job returns immediately.
- **`app/backup_storage.py`** — `get_object(key) -> bytes` alongside
  `presigned_put_url`, 64 MB cap, raises `R2NotConfigured` / `ValueError`.

### 5. Routers — DONE
- **`app/routers/tally.py`** (`/api/tally`, `WriteUser`):
  `GET|POST /company`, `PUT /company/ledger-map` (partial, `extra="forbid"`,
  empty string clears a slot), `POST /pull-masters` (409 in-flight),
  `GET /sync-jobs`, `GET /sync-jobs/{id}`, `POST /sync-jobs/{id}/retry`
  (only a failed job, re-enqueues).
- **`app/routers/tally_agent.py`** — `POST /jobs/{id}/result`
  (`ShopAuth`; body `{status, r2_key?, error?, file_mtime?}`; 403 if the
  job's company isn't served by this shop; delegates to
  `process_pull_result`; always 200 to the agent).
- **`app/schemas_tally.py`** — `TallyCompanyIn/Out`, `LedgerMapIn`,
  `TallySyncJobOut`, `JobResultIn`, `KnownLedger`.
- Registered in `app/main.py`.

### 6. Tests — DONE
`tests/test_tally_connector.py` (7) + fixture
`tests/fixtures/tally_connector_pull_sample.xml` (3 debtor/creditor
ledgers with +/- opening balances, 2 stock items, 1 dummy, plus Sales/CGST
ledgers that must NOT stage). Covers: company CRUD + ledger-map
partial/clear/unknown-key; `pull-masters` requires `shop_id`; full flow
(enqueue → outbox → checkin dispatch-once → result → 3 parties + 2 items
staged + `sync_job_id` tagged + `known_ledgers` derived); second pull 409
while in-flight and 409 while awaiting review; error path + idempotency;
wrong-shop callback → 403; missing `r2_key` → job error. R2 monkeypatched.

### 7. Scheduler — NOT in F1a
On-demand only (button + retry). A nightly auto-pull needs the same
scheduler decision as F3c (none in the repo). Defer.

---

### Staging — reuse the existing tables (LOCKED, done)
Reuse `staging_tally_party` + `staging_tally_item` and their existing
review/commit UI (the ImportPage), driven by a `tally_sync_job` instead of
a file upload. The pull writes staged rows exactly as the upload path does;
the operator reviews in the **existing** ImportPage; "commit" runs the
**existing** commit path. F1a's only new UI is the "Pull masters" button
+ job status + the Tally tab.

- `sync_job_id` on both staging tables; `pull-masters` + both file-upload
  routes call `assert_no_pull_in_flight`; `tally_sync_job` has the
  `(tenant_id, status)` index. Multi-firm is isolated by `tenant_id` as
  everywhere else. (All built — see §3 above.)

---

## tally-agent (.NET) changes — PENDING

Files: `tally-agent/TallyAgent/`. The `IAgentModule` seam, `BackendClient`,
`TallyGatewayClient` (already has `ExportAsync`), and the checkin loop all
exist. **The one piece of shared infra missing: nothing in the agent reads
the checkin response's `outbox`.** `TallyAgentService` calls checkin purely
as a heartbeat and discards the response. F1a adds outbox draining + one
module.

### 1. Outbox plumbing (shared) — `AgentContext` + `TallyAgentService`
The host calls checkin *after* the module round, so a module can't see this
round's outbox. Minimal fix: after `CheckinAsync` returns,
`ctx.SetPendingOutbox(resp?.Outbox ?? [])`; modules read `ctx.PendingOutbox`
next round. One poll of latency — fine at the 1-min checkin cadence, and
F1a's states retry anyway.
- `AgentContext`: `IReadOnlyList<OutboxItem> PendingOutbox` +
  `SetPendingOutbox(list)`.
- `TallyAgentService.ExecuteAsync`: capture the `CheckinAsync` return (it's
  currently fire-and-forget) and push it onto `ctx`.

### 2. `Modules/TallyMastersModule.cs` (`IAgentModule`)
- `Name = "tally"`, `PollInterval = 1 min`.
- `RunOnceAsync`: for each `ctx.PendingOutbox` item with `Module == "tally"`
  and `payload["action"] == "pull_masters"`:
  - **Gateway first.** `POST` the All-Masters export envelope (below) to
    `AgentOptions.TallyMasters.GatewayUrl` (default `http://localhost:9000`)
    via `ctx.TallyGateway.ExportAsync`.
    - Response contains `<STATUS>1</STATUS>` → that body IS the masters XML.
    - `LINEERROR ... Could not find Company` → module status
      `no_company_loaded`, **leave the job** (don't post a result — a later
      poll retries when the accountant opens the company).
    - `HttpRequestException` / timeout → module status `tally_unavailable`,
      leave the job.
  - **Fallback:** if the Gateway path failed *and*
    `AgentOptions.TallyMasters.ExportDir` is set → read the newest `*.xml`
    there. Empty / unset → the module-status above stands.
  - Got XML → `ctx.Backend.RequestUploadAsync(name, size)` → `PutFileAsync`
    → `ctx.Backend.PostJobResultAsync(jobId, "ok", r2Key)`  (**new** method).
  - Any exception in the upload/report path → `PostJobResultAsync(jobId,
    "error", null, ex.Message)` + module status `error`. **Never throws.**
  - `jobId = payload["job_id"]` — `payload` is `Dictionary<string,object?>`,
    values arrive as `JsonElement`; small `GetString(payload, key)` helper.

Export envelope constant (verified live, 625 KB response, `parse_masters`
handles it unchanged):
```xml
<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>
 <TYPE>Data</TYPE><ID>List of Accounts</ID></HEADER>
 <BODY><DESC><STATICVARIABLES>
  <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  <SVCURRENTCOMPANY>{companyName}</SVCURRENTCOMPANY>
  <ACCOUNTTYPE>All Masters</ACCOUNTTYPE>
 </STATICVARIABLES></DESC></BODY>
</ENVELOPE>
```
`{companyName}` from `payload["company_name"]` (the operator's
`tally_company.company_name`); omit the line to use whatever company is
open.

### 3. `BackendClient.PostJobResultAsync(jobId, status, r2Key?, error?)`
`POST api/tally-agent/jobs/{jobId}/result` with `{status, r2_key, error}`.
~12 lines, mirrors `ConfirmUploadAsync`. + `JobResultRequest` in
`BackendModels.cs`.

### 4. Config — `AgentOptions.cs`
```csharp
public sealed class TallyMastersOptions
{
    public bool Enabled { get; set; } = true;
    public string GatewayUrl { get; set; } = "http://localhost:9000";
    public string ExportDir { get; set; } = "";   // optional fallback
    public int PollIntervalMinutes { get; set; } = 1;
}
```
+ `public TallyMastersOptions? TallyMasters { get; set; }` on `AgentOptions`.

### 5. `Program.cs`
`builder.Services.AddSingleton<IAgentModule, TallyMastersModule>();`

### 6. Installer
`tools/tally_agent_installer.py` (backend, builds the per-shop
`appsettings.json` on download) + `install.ps1` template gain a
`TallyMasters` section. `GatewayUrl` default is fine; `ExportDir` blank
unless a shop needs the fallback.

**Not in the agent for F1a:** no state-DB tracking (a re-upload of the same
masters XML is harmless — the backend job is the idempotency point); no
file-stability check; no voucher push.

---

## Frontend changes (Ops console) — BUILT 2026-09-08

`web/src/pages/admin/TallyPanel.tsx` (new) + one line in `FirmDetailPane`
(`FirmsPage.tsx`) + `web/src/lib/types.ts` (Tally types) +
`web/src/pages/admin/api.ts` (the `/admin/firms/{id}/tally/*` calls).
tsc + eslint + `vite build` all clean.

Built as specced below **except** the "Review N →" deep-link:

- **Review is the firm's job, not the operator's.** The staging tables and
  the ImportPage (`/parties/import`, `/items/import`) are `WriteUser`-scoped
  — the firm's own login, not the platform-admin. A pull stages the batch;
  the panel then shows *"N parties + M items staged. The firm reviews and
  commits under Parties → Import / Items → Import in their own login."*
  The firm's ImportPage already resumes an un-committed batch via
  `GET /parties/import/current`, so no new review UI on either side. This
  matches the platform-admin split (operator provisions, firm operates) and
  is why F1a adds **zero** review code.
- Consequence: while a pulled batch sits un-reviewed, `pull-masters` 409s
  ("waiting for review"). The operator sees the `ok` sync-job row with a
  `batch_id` as the signal that the firm still owes a commit.

Everything else matches the spec:

---

**The `Tally` section, stacked on the firm-detail pane** (below
`WhatsappPanel`)
(`web/src/pages/admin/FirmsPage.tsx` → `FirmDetailPane`, below
`WhatsappPanel`). Same panel idiom as `WhatsappPanel` — a
`border-b border-line py-5` block, uppercase `<h3>` + a status pill, no
tabs (the admin pane has never had them). Visual review:
`docs/visual-plan/tally-connector-f1a-review.html`. Design decisions
locked from that review (Q1–Q5):

- **Connection sub-block:** `company_name` input + `shop_id` picker (from
  `GET /api/tally-agent/admin/shops`). Autosave on blur. The status pill
  shows **live agent reachability** (Q1) — from `BackupShop.last_checkin_at`:
  `Linked · checked in 40s ago` (green) / `Linked · no check-in for 3 days`
  (warn) / `Not linked` (grey). No export-folder field (Gateway transport).
- **Ledger-map sub-block — collapsed by default** (Q: it's F1b's). A
  "Set up invoice-push ledgers →" disclosure expands to the
  sales / round-off / CGST / SGST / IGST / debtors-group / creditors-group
  `<select>`s, options from `company.known_ledgers` (disabled with "run a
  pull to load ledger names" before the first pull). Only
  `debtors_parent` / `creditors_parent` matter for F1a (default
  "Sundry Debtors" / "Sundry Creditors").
- **Pull sub-block:** "Pull masters now" → `POST /api/tally/pull-masters`.
  A **step indicator** (Q3), not a chip: `Queued → Agent picked it up →
  Exported from Tally → Parsed → Ready` — filled / current-pulsing /
  hollow. Maps from `job.status` + the agent module-status:
  - `queued` → step 1
  - `sent` → step 2 (agent has the outbox item)
  - `sent` + agent status `no_company_loaded` / `tally_unavailable` →
    **still step 2, sub-label "Waiting on Tally — it's closed or no company
    is open"**
  - `running` → step 3–4 (backend downloading + parsing)
  - `ok` → step 5, + "Review N parties + M items →" deep-link into the
    existing ImportPage for `job.batch_id`
  - `error` → the step it died on turns red + the message + Retry
  Polled from `GET /api/tally/sync-jobs/{id}` — **15 s while
  queued/waiting, 5 s while running** (Q2). Button disabled (409-derived
  note) while a pull is in flight or awaiting review.
- **Recent pulls table:** **last 10** jobs (Q4). When / status / parties /
  items / detail. On an `error` row: a **"Download the XML"** link (Q5) →
  `GET /api/tally/sync-jobs/{id}/xml` (streams `get_object(job.r2_key)`,
  platform-admin only) so a Fleek engineer can debug a parse failure
  against the actual file. Success rows have no drawer. Retry button on
  `error`.

`web/src/lib/types.ts` — `TallyCompany`, `TallySyncJob`, `KnownLedger`.
`web/src/pages/admin/api.ts` — the `/api/tally/*` calls.

No change to the invoice editor or any customer-facing screen in F1a.

### Backend deltas this review added (not yet built)
- **Auto-cancel a stuck pull** (Q3 tail): a `pull_masters` job that has been
  `sent` with an agent "waiting" status for **> ~10 check-ins (~10 min)** is
  moved to `error` ("Tally stayed unavailable — retried, then cancelled"),
  freeing the tenant to start a new pull. Implemented as a check in
  `checkin` (it already touches every `tally` job) or in
  `assert_no_pull_in_flight` (lazy-expire on the next pull attempt) —
  **lazy-expire is simpler and needs no new counter**: on `pull-masters`,
  if the in-flight job is `sent`, older than 10 min, and its outbox item
  is still `sent` (agent saw it, never resolved), expire it and proceed.
- **`job.error` from the agent's "waiting" status** — the agent currently
  *doesn't* post a result for `no_company_loaded` / `tally_unavailable`
  (leaves the job for retry). The step indicator's "Waiting on Tally"
  sub-label needs that reason surfaced: add an **agent status ping** —
  `POST /api/tally-agent/jobs/{id}/status {agent_status}` (ShopAuth,
  writes a `last_agent_status` + `last_agent_status_at` column on
  `tally_sync_job`) that the module calls instead of silently returning.
  Migration `0023` (2 columns) or fold into `0022` if it hasn't shipped.
- **`GET /api/tally/sync-jobs/{id}/xml`** — new, platform-admin, streams
  the R2 object for a failed job.
- `TallySyncJob` gains `last_agent_status` / `last_agent_status_at`
  (nullable).

---

## Tests

### Backend — DONE (`tests/test_tally_connector.py`, 7 tests)
Covers company CRUD + ledger-map partial/clear/unknown-key; `pull-masters`
requires `shop_id`; full flow (enqueue → outbox → checkin dispatch-once →
result → parties + items staged + `sync_job_id` tagged + `known_ledgers`
derived); 409 in-flight + 409 awaiting-review; error path + idempotency;
wrong-shop callback 403; missing `r2_key` → job error. See the fixture
`tests/fixtures/tally_connector_pull_sample.xml`.

### Backend — TO ADD for the review deltas
- Agent status ping: `POST /jobs/{id}/status` writes `last_agent_status`;
  wrong shop → 403.
- Lazy auto-cancel: a `sent` job older than the window with an unresolved
  outbox item is expired to `error` on the next `pull-masters`, which then
  succeeds.
- `GET /sync-jobs/{id}/xml`: platform-admin only; streams the stubbed R2
  object for a failed job; 404 for a job with no `r2_key`.

### tally-agent (.NET)
- `GetString` helper: pulls `job_id` / `company_name` out of a
  `Dictionary<string,object?>` whether the value is a raw string or a
  `JsonElement`.
- Module: gateway body containing `Could not find Company` → posts a status
  ping, no result, job untouched; `<STATUS>1</STATUS>` body → upload +
  `ok` result; folder fallback picks the newest `*.xml`.

---

## Migration / deploy

**Infra first (`fleek-infra`, branch `master` — push before Metal ERP;
memory `infra-alignment`).** DONE 2026-09-08, uncommitted in
`c:\Fleek Finance Code\infra`. **No new Vault exports** — `r2/shared` is
already exported unconditionally by `load-vault-secrets.sh` as
`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` (for postgres-backup); the app
just reads them under its own `TALLY_R2_*` names.

- `docker-compose.yml`, `metalerp-api` `environment:` — 4 vars added:
  ```
  TALLY_R2_ENDPOINT_URL: ${R2_ENDPOINT_URL}                    # existing plain config
  TALLY_R2_ACCESS_KEY_ID: ${R2_ACCESS_KEY_ID}                  # existing r2/shared export
  TALLY_R2_SECRET_ACCESS_KEY: ${R2_SECRET_ACCESS_KEY}          # existing r2/shared export
  TALLY_R2_BUCKET: ${METALERP_TALLY_R2_BUCKET:-metalerp-tally}
  ```
- `.env.example` — documents `METALERP_TALLY_R2_BUCKET=metalerp-tally`.
- `load-vault-secrets.sh` — **unchanged.**
- Validated with `docker compose config` (parses; endpoint + bucket
  resolve).
- **Manual, one-time:** create the `metalerp-tally` bucket in the
  Cloudflare R2 account (`r2/shared` creds can write to any bucket there).
  *Or* set the compose line to `${R2_BACKUP_BUCKET}` to reuse
  `fleek-backups` under a per-shop-id prefix — no new bucket. This also
  covers the F10 tally-agent-backup slice's open "R2 bucket" item.

**Then:**
1. Push `fleek-infra` `master`. Deploy webhook redeploys `metalerp-api`
   with the four `TALLY_R2_*` vars.
2. Push Metal ERP — `alembic upgrade head` runs `0022` on container start.
3. Build + ship the updated tally-agent (`dotnet publish -r win-x64`) to
   `settings.tally_agent_build_dir`. Pilot installs: manual update is fine
   (F10 auto-update story still open).
4. **One-time per pilot firm, all from the Ops console:**
   a. Open the firm → **Shop agent** → "Set up the shop agent" → copy the
      key shown.
   b. Install the tally-agent build on the shop's Windows PC; paste the key
      into `appsettings.json` as `Agent:ShopApiKey`; set
      `Agent:TallyMasters:GatewayUrl` (default `http://localhost:9000` is
      usually right) and enable "TallyPrime acts as Server" in
      `F1 → Settings → Connectivity`.
   c. Back in the console → **Tally** → enter the Tally company name →
      "Pull masters now".
   No CLI, no SSH.

---

## Blocking pre-work — RESOLVED 2026-09-08

1. ~~Pilot Tally audit / transport choice~~ → **decided: folder-read only
   for F1a.** No Gateway. The audit (edition, multi-company) still matters
   for F1c but does not block F1a.
2. ~~R2 bucket + creds~~ → **decided: reuse `r2/shared` Vault creds, new
   hard-coded bucket `metalerp-tally`** (see Migration / deploy).
3. ~~Ledger enumeration format~~ → **dropped.** `known_ledgers` is derived
   from the masters-pull XML itself; no separate report/envelope needed.

Nothing blocks starting the build. Backend-first order:
models + `0022` → `apply_masters` service (against existing parser +
fixtures) → routers → agent module → Ops UI.

---

## Not in this slice

- **Any voucher push** (sales / receipt) — F1b.
- **Live HTTP sync to Tally** (agent POSTs `http://localhost:9000`) — F1c.
  F1a's agent module does **not** touch the Gateway at all; folder-read is
  the whole transport.
- **Scheduled / automatic pulls** — on-demand button only. F1c/F1d.
- **Ledger-group (accounting group) sync**, currencies, cost centres —
  only Sundry Debtors/Creditors lineage for party scoping.
- **Conflict resolution** when a master changed on both sides — F1a is
  blank-fill only, so there's no overwrite to conflict; a real two-way
  merge policy is F1d.
- **Multi-company / multi-GSTIN** per tenant — `tally_company` is
  `UNIQUE(tenant_id)` for M1.
- **`item.default_rate` from Tally standard price as an authoritative
  update** — blank-fill only, same as everything else.

# EXECUTION PLAN — F5d: Purchase-voucher-out (live push, non-GST-branch reuse)

Status: **BUILT 2026-09-09.** Third live-push slice of the Tally
connector, after F1a (masters-in, live e2e confirmed) and F1b-1 (sales
voucher push, built + live-verified, uncommitted). Pushes an **approved**
Inward Bill into the shop's Tally as a `Purchase` voucher, automatically,
via the same agent/job transport F1a/F1b-1 built.

**Live probe done before building (2026-09-09):** fired a hand-written
Purchase voucher with a standalone Round Off `LEDGERENTRIES.LIST` line
directly at the dev box's live TallyPrime over HTTP — `CREATED=5,
EXCEPTIONS=0`. **Resolves this plan's one flagged open question: F1b-1's
Finding 2 (a standalone Round Off line silently breaks a live-pushed Sales
voucher) does NOT carry over to Purchase vouchers on this install** — the
existing `inward/tally_xml.py` shape (separate Round Off ledger line) was
used as-is for live push, no redesign needed, only the encoding parameter.

**Backend built:** ledger-config convergence (migration `0028`, data-only
merge of `tally_ledger_config` into `tally_company.ledger_map`;
`purchase_ledger` added to `LedgerMapIn`/`_LEDGER_MAP_KEYS`);
`purchase_push_blockers()` in `push_readiness.py` (strict mode — a
staged-new supplier/item blocks the same as an unlinked one);
`enqueue_push_purchase()` + `bill_checksum()` +
`assert_no_purchase_push_in_flight()` in `jobs.py`, reusing
`complete_push_ok` (now entity-generic off `job.entity_type`);
`process_push_result` in `push.py` generalized to dispatch on `job.kind`
(voucher-type-agnostic response parsing, only the entity lookup differs);
`GET/POST /api/inward-bills/{id}/tally/push-status` + `/push` routes;
best-effort auto-enqueue hook in `approve.py` (`_enqueue_tally_push_best_effort`,
same non-blocking try/except pattern as `finalize.py`); `tally_sync_status`
on `InwardBillListItem` via the same window-function join pattern as
`InvoiceListItem`. `tally_agent.py`'s job-result dispatch widened to
`("push_sales", "push_purchase")`.

**Agent (.NET) built:** two one-line changes — `TallyMastersModule.cs`'s
action-switch now also routes `push_purchase` to the existing
`ProcessPushAsync` (unchanged otherwise — it was already voucher-type-
agnostic, exactly as this plan predicted). `dotnet build TallyAgent.slnx`
— 0 warnings, 0 errors.

**Tests:** `tests/test_inward_tally_xml.py` (new, 3 — UTF-16 default
preserved for file-export, UTF-8 override produces identical element
structure, ledger names flow through from a `ledger_map`-style config) +
`tests/inward/test_inward_tally_push_integration.py` (new, 15 — blockers
per code including strict-mode staged-new supplier/item and `not_approved`,
enqueue+idempotency+voucher-XML-in-payload, `process_push_result` ok/
LINEERROR, router 422/201/404, approve-hook no-ops when items are still
staged-new post-approve and when Tally is unreachable). Full suite: **495
passed, 2 skipped** (up from 480 at F1b-1's own commit — +15 new F5d
tests). Ruff clean, mypy clean on every touched file (the one remaining
mypy hit, `tools/tally_import/item_match.py`, is pre-existing and
untouched by this slice).

**Not yet done:** git commit (nothing in this session's F5d or F1b-1 work
is committed yet — per project convention, commits happen on request);
live e2e through the real compiled agent binary (same gap F1b-1 itself
still carries — the wire payload and direct HTTP calls are proven, the
actual checkin→push→Tally loop through the compiled binary is not); manual
cleanup of the `ZZTEST Purchase*`/`ZZTEST-PUR-1` test masters/voucher left
in the live "Fleek" Tally company from this session's live probe.

## Where this sits in F5 (`MASTER-tally-complement-saas.md` §F5)

F5 (AP Bill Capture) has five real ingest/record questions and one push
question. This slice is **only the push question** — F5d in the master
doc's own slicing (F5a re-skin+ingest channels, F5b LLM fallback, F5c
payable+ageing, F5d Tally push). The other four slices are separate work,
not blocked by this one and not part of it.

**Explicitly decided out of scope (session discussion, 2026-09-09):**
manual (no-document) purchase entry. The accountant already has Tally open
and can type a purchase voucher directly — there's no capability gap the
way there is for owner-mobile sales billing. Re-keying into a second
system for no reason invites exactly the two-system drift F1 exists to
prevent. Purchases with no document stay in Tally, entered by the
accountant as today. Metal ERP's AP-capture value is specifically: PDF/
photo parsing, WhatsApp-in, email-in — cases that would otherwise be
**manual re-typing work a human does anyway**, which this pipeline removes.

## Why this is smaller than it sounds (same shape as F1b-1)

Everything F1b-1 needed already exists and is now proven twice:

- Transport: `AgentOutboxItem`, `TallySyncJob`, checkin dispatch,
  `tally_reachable` gate, `TallyLink` — entity-generic, already handles
  `entity_type='invoice'`; adding `entity_type='inward_bill'` is additive.
- Agent: `TallyMastersModule.RunOnceAsync`'s action-switch dispatch and
  `ProcessPushAsync` already POST an arbitrary voucher XML string and
  return Tally's raw response unparsed. **This code needs zero changes
  for a purchase voucher** — it doesn't know or care what kind of voucher
  it's carrying.
- Serializer: `app/services/inward/tally_xml.py` already exists,
  already builds a correct Purchase voucher (GST split, bill-wise
  allocation, UDF ref, new-supplier/new-item master-create) — it was
  F1b-1's own template. It currently targets **file export only**
  (`xml_encoding: str = "UTF-16"` default, written to a volume for manual
  import per its docstring).

So this slice is: **one encoding/live-push fix to existing code, one new
push-readiness gate, one new enqueue function, one approve-time hook, one
frontend status component (near-identical to `TallySyncStatus.tsx`), and
the ledger-config convergence decided below.** No new agent code, no new
transport, no new job-lifecycle concepts.

---

## Decision: converge `tally_ledger_config` into `tally_company.ledger_map`

**Locked 2026-09-09.** F1b-1's own "Not in this slice" list flagged these
as two unconverged configs. Comparing the two:

```
tally_ledger_config (F5, one row per tenant, PK=tenant_id)
  creditors_group, purchase_ledger, cgst_ledger, sgst_ledger,
  igst_ledger, round_off_ledger, xml_encoding

tally_company.ledger_map (F1a/F1b, JSON on the one-row-per-tenant company)
  sales_ledger, cash_ledger, bank_ledger, round_off_ledger,
  cgst_ledger, sgst_ledger, igst_ledger,
  debtors_parent, creditors_parent
```

`ledger_map` is already a strict superset except `purchase_ledger` and
`creditors_group` (== `creditors_parent`, same concept, different name).
Converging avoids a firm configuring GST ledger names in two different
screens that can silently drift apart — a real support-cost risk, not a
cosmetic one.

**Plan:**
1. Add `purchase_ledger` to the `ledger_map` JSON shape (default
   `"Purchase Accounts"`, same as `tally_ledger_config`'s default).
   `creditors_parent` already exists on `ledger_map` — inward's `approve.py`
   switches from `creditors_group` to it (same value, F1a already collects
   it for party-pull scoping, so this is likely already correctly filled in
   for any firm that's done an F1a masters pull).
2. `xml_encoding` is **not** carried over as a per-tenant setting — same
   reasoning as F1b-1 Finding 3: encoding is a property of the *transport*
   (file = UTF-16, live HTTP push = UTF-8), not something an operator
   should configure per firm. `build_xml_bytes()` keeps an `encoding`
   **parameter** (function-level, not config-level) so the existing file-
   export route (`GET /inward/{id}/tally.xml`) keeps working unchanged
   (still defaults UTF-16) while the new live-push path passes UTF-8
   explicitly.
3. `routers/inward.py`'s `GET`/`PUT /settings/ledgers` — **retire**,
   replaced by pointing the inward ledger-settings UI at the same
   `tally_company` ledger-map editor F1a/F1b's `TallyPanel.tsx` already
   has (add `purchase_ledger` as one more field there). One config screen
   per firm, not two.
4. **Migration:** a data-migration step copies any existing
   `tally_ledger_config` row's `purchase_ledger` (and `creditors_group` if
   ever set to something non-default) into the matching tenant's
   `tally_company.ledger_map` JSON, for tenants that have both rows. Most
   tenants won't — F5's ledger config only gets created lazily on first
   `GET`/`PUT /settings/ledgers` (`routers/inward.py:474`), and F1a/F1b
   only exists for the pilot firm so far — but write the backfill
   correctly regardless. Drop the `tally_ledger_config` table in a
   follow-up migration *after* confirming nothing references it (keep it
   one release behind, don't drop-and-migrate in the same deploy).
5. `approve.py` (`build_xml_bytes` caller) switches from reading
   `TallyLedgerConfig` to reading `tally_company.ledger_map` — if no
   `tally_company` row exists for the tenant yet (F1a never set up), fall
   back to the same hardcoded GST-standard defaults `TallyLedgerConfig`
   used, so bills can still be approved and file-exported for firms that
   haven't done F1a onboarding. **Live push (this slice) requires a real
   `tally_company` row anyway** (same as F1b-1's `no_tally_company`
   blocker) — the fallback only matters for the pre-existing file-export
   path staying backward compatible.

---

## Scope decision (locked, mirrors F1b-1)

**Strict mode.** Same as F1b-1: push only if the resolved supplier
(`bill.matched_party_id`) and every resolved item already carry a
`tally_guid`. A bill referencing a **staged new** supplier or item
(`new_supplier_staged_json` / `new_item_staged_json` still set,
i.e. approve created it locally but it was never pushed as a master) is
**not auto-created in Tally** by this slice — same reasoning as F1b-1's
strict mode: master-create roughly doubles serializer surface and
shouldn't gate proving the voucher path. Flagged, push skipped, same
"pull masters again after adding it in Tally, or push masters explicitly"
recovery path.

This is a **real, expected gap for F5** specifically — unlike sales
(where most parties/items already exist from normal shop operation before
F1a's pull), a purchase bill's supplier or item is disproportionately
likely to be genuinely new (a first-time vendor, a new stock line). Expect
`item_not_linked`/`party_not_linked` blockers to fire *more* often here
than on F1b-1's invoices. **F5-e (master auto-create at push time,
deferred, same as F1b-1's F1b-2)** is a stronger candidate for a fast
follow-up here than it was for sales — flag this expectation to the user
once live data shows how often it actually fires, don't pre-build it.

**GST-aware, unlike F1b-1.** Inward bills are real vendor bills — CGST/
SGST/IGST are live fields today (`inward/tally_xml.py` already branches on
`supply_type`), not dormant the way sales-side tax columns are. No new
branch to write; the existing GST logic just needs the live-push encoding
fix, not a new tax model.

**Manual re-push only.** Same as F1b-1 — no scheduler, agent-checkin retry
is the only automatic retry.

---

## Data model — next migration after F1b-1's `0027` (verify `alembic heads`)

- `tally_company.ledger_map` JSON gains `purchase_ledger` key (no schema
  change, JSON column already exists).
- `tally_sync_job.kind` gains `'push_purchase'` (already free-text).
- `tally_sync_job.entity_type` gains `'inward_bill'` value (column already
  added by F1b-1's `0027`, no new migration needed for this alone).
- **New migration** for the `tally_ledger_config` → `ledger_map` backfill
  (data-only, see convergence plan above) — table drop deferred to a later
  release.

```
00XX_purchase_voucher_push.py
  -- data migration: for each tenant with both tally_ledger_config and
  -- tally_company rows, merge purchase_ledger (+ creditors_group if
  -- non-default) into tally_company.ledger_map JSON.
  -- no column changes.
```

---

## Backend changes

### 1. Serializer — `api/app/services/inward/tally_xml.py` — small edit, not new

- `LedgerConfig.xml_encoding` default **stays UTF-16** (file-export path
  unchanged) — the live-push call site passes `encoding="UTF-8"`
  explicitly to `build_xml_bytes()`, same pattern as F1b-1's sales
  serializer. No default flip here, since the existing file-export route
  must keep working byte-for-byte for firms mid-way through the old
  manual-import flow.
- `LedgerConfig` gains no new fields — `purchase_ledger`/`creditors_group`
  already exist on the dataclass; the **caller** now builds a `LedgerConfig`
  from `tally_company.ledger_map` instead of `TallyLedgerConfig` (see
  convergence plan).
- `_party_name_hint()` currently a stub returning `None` (line 200-204) —
  **needs a real implementation** for live push: the party's actual
  `legal_name` at push time, same as F1b-1 reads `party.legal_name`
  directly. Today `build_xml_bytes(party_name=...)` is passed in
  explicitly by the caller and monkey-patches the XML after the fact
  (lines 234-242) — keep that mechanism, just confirm the live-push
  caller supplies it (it already does, per `approve.py`'s existing usage —
  verify, don't assume).

### 2. Push-readiness — `api/app/services/tally/push_readiness.py` — extend

Add `purchase_push_blockers(session, bill: InwardBill) -> list[PushBlocker]`
alongside the existing `push_blockers()` (rename the sales-specific one to
`sales_push_blockers` for symmetry, or keep both under one dispatching
function — decide at build time, not a design question worth blocking on).

- `no_tally_company` — same check.
- `ledger_map_incomplete` — requires `purchase_ledger` (mirrors sales
  requiring `sales_ledger`); `round_off_ledger` **is** required here
  (unlike sales) since the purchase voucher's existing, proven shape uses
  a real standalone Round Off `LEDGERENTRIES.LIST` line (line 184-185 of
  the existing serializer) — F1b-1's Finding 2 (round-off breaks a
  standalone ledger line) was specific to **invoice-mode Sales vouchers on
  this dev box's edition/config**; the Purchase voucher's existing shape
  already round-trips fine today via file import, so **do not
  speculatively change it** — verify live before assuming Finding 2
  applies here too (see Testing/live-verification section below).
- `party_not_linked` — `bill.matched_party_id is None` (staged-new
  supplier not yet in Tally) **or** the matched party's `tally_guid is
  None`.
- `item_not_linked` — any resolved line's item missing a `tally_guid`, or
  a line still carrying `new_item_staged_json` (never pushed as a master).
- `not_reconciled` / `not_approved` — a bill must be `status == approved`
  (approve_gate already enforces reconciliation + resolution as a
  precondition to reach `approved` at all, so this is mostly a defensive
  "don't push a draft" check, not new business logic).

### 3. Job lifecycle — extend `api/app/services/tally/jobs.py`

- `enqueue_push_purchase(session, company, bill: InwardBill) -> TallySyncJob`
  — mirrors `enqueue_push_sales` exactly: blockers check → 422; in-flight
  check → 409; build XML at enqueue time (**same bug class F1b-1 caught
  once already** — do not create the job/outbox row before the XML is
  built and attached, write a test asserting the payload is well-formed
  XML from day one, don't rediscover this gap); idempotency via
  `TallyLink(entity_type='inward_bill', entity_id=bill.id).checksum`
  (hash of grand_total + line count + line-total sum, same shape as
  invoices).
- `complete_push_ok` / `complete_push_error` — **reuse F1b-1's, entity-
  generic already** (`entity_type`/`entity_id` on `TallyLink` and
  `TallySyncJob` were built generic in F1b-1's own `0027` migration) — no
  new functions needed here, just call sites with `entity_type=
  'inward_bill'`.

### 4. Result processing — `api/app/services/tally/push.py` — reuse, verify generic

`process_push_result` was written against Sales vouchers but its actual
logic (`<CREATED>`/`<LINEERROR>`/`LASTVCHID` extraction) is voucher-type-
agnostic — it parses Tally's generic Import-Data response shape, not
anything Sales-specific. **Read it before assuming** — if it silently
assumes `kind == 'push_sales'` anywhere (e.g. in a log message or a
narrower field write), generalize; otherwise this needs no code change,
just confirm via a purchase-specific test.

### 5. Router — extend `api/app/routers/inward.py` (not `tally.py` —
these are inward-bill-scoped, follow the existing router split)

```
POST /api/inward/{bill_id}/tally/push
  -> enqueue_push_purchase; 422 blocker list; 409 in-flight/not-reachable
GET  /api/inward/{bill_id}/tally/push-status
  -> latest job + purchase_push_blockers() (so UI shows "why not" pre-job)
```

Tenant-scoped already (inward router's existing `InwardUser`/
`InwardWriteUser` pattern) — no separate self-serve/admin split needed
here the way F1b-1 needed (inward has always been tenant-scoped, unlike
invoices which also have an Ops-console admin path).

### 6. Approve hook — `api/app/services/inward/approve.py` — extend

After the existing step 5 (build XML → write to volume →
`tally_xml_path`), add a best-effort live-push enqueue, **same non-
blocking try/except pattern as `finalize.py`**:

```python
_enqueue_tally_push_best_effort(session, bill)
```

- No-ops silently if `purchase_push_blockers()` non-empty (expected common
  case, especially "new supplier/item not yet a Tally master" — see scope
  decision above, expect this to fire often for F5 specifically).
- Never raises into the approve flow — approve must succeed regardless of
  Tally state, exact same contract finalize.py already established.
- **Keep the existing file-XML write** (`tally_xml_path`) unconditionally,
  even when live push also succeeds — some firms/accountants may prefer
  the manual-import safety net, and it costs nothing to keep generating
  it. Live push is additive, not a replacement for the file path in this
  slice.

### 7. Inward bill list/detail — sync status

Same `tally_sync_status` aggregate-join pattern as
`InvoiceListItem`/`routers/invoices.py` — add to whatever the inward bill
list schema/router already returns, joined against
`TallySyncJob(entity_type='inward_bill')`.

---

## Frontend changes

- **New `components/TallySyncStatus.tsx` reuse, not a fork** — the
  existing `TallySyncBadge`/`TallyPushPanel` components are already
  generic over `invoiceId` as a prop name only; genuinely worth
  generalizing the prop to `entityId`/`entityType` (or a thin
  `InwardTallyPushPanel` wrapper calling the same two hooks against
  `/inward/{id}/tally/push-status` and `/inward/{id}/tally/push`) rather
  than duplicating the component. Decide the cheaper refactor at build
  time — the component's internals (query/mutation/render) don't change,
  only the URL template.
- Inward bill list: reuse `TallySyncBadge` verbatim (already takes a
  bare `status` prop, no invoice-specific coupling).
- Inward bill detail page: mount the push panel the same way
  `InvoiceEditorPage.tsx` does.
- Ledger-settings UI: retire the inward-specific ledger form (per the
  convergence decision), add `purchase_ledger` as one more field on
  `TallyPanel.tsx`'s existing ledger-map editor.

---

## Tests

Backend (`api/tests/test_inward_tally_push.py` — new, mirrors
`test_invoice_tally_push.py` + `test_invoice_tally_push_integration.py`
split):
- Golden-XML: live-push encoding (UTF-8) produces the same element
  structure as the existing file-export golden test, only the
  `<?xml?>` declaration's encoding differs.
- `purchase_push_blockers()`: each code in isolation, including the two
  purchase-specific ones (staged-new supplier, staged-new item) that
  don't exist on the sales side.
- `enqueue_push_purchase`: 422/409/idempotent-noop, **payload actually
  contains the voucher XML** (assert this explicitly — the exact gap
  F1b-1 caught once).
- Convergence migration: a tenant with a pre-existing `tally_ledger_config`
  row (non-default `purchase_ledger`) ends up with that value present in
  `tally_company.ledger_map` post-migration.
- Approve hook: fully-linked bill on a configured firm auto-enqueues;
  missing precondition → approve still succeeds, no job.

**Live verification against real Tally, before trusting the round-off
assumption above:** push one real approved bill (with a nonzero round-off)
through the live-push path against the dev box's TallyPrime, the same way
F1b-1 fired ~20 probe variants before writing code. Specifically confirm
whether Finding 2 from F1b-1 (standalone Round Off ledger line breaks an
invoice-mode voucher) also applies to `VCHTYPE="Purchase"` — the existing
serializer's shape (separate Round Off `LEDGERENTRIES.LIST` line) has
never been tested through the *live HTTP* gateway, only through file
import, which may not trigger the same failure mode Finding 2 found.
**Do not assume Finding 2 carries over — verify, since the two voucher
types differ enough (Sales is `PERSISTEDVIEW=Invoice Voucher View` for
inventory-linked entry; Purchase already works via file import with the
separate line) that the failure could be transport-specific (Finding 3
territory) rather than voucher-shape-specific (Finding 2 territory).**

.NET: no agent-side change expected (see "why this is smaller than it
sounds") — confirm with a `dotnet build` after the backend lands, don't
expect to write new C#.

---

## Not in this slice

- Master auto-create at push time (staged new supplier/item) — F5-e,
  likely a faster follow-up here than F1b-2 was for sales, given the
  scope-decision note above about how often it's expected to fire.
- New ingest channels (WhatsApp-in, email-in, camera capture) — F5a/b,
  independent of this slice; this slice's push works identically
  regardless of how the bill was ingested, since it operates on `approved`
  `InwardBill` rows regardless of `extraction_method`.
- Payable record + ageing (F5c) — independent, no dependency either way.
- Dropping the `tally_ledger_config` table — deferred one release behind
  the data-migration, per the convergence plan.
- Manual (no-document) purchase entry — explicitly decided out, see top
  of this doc.

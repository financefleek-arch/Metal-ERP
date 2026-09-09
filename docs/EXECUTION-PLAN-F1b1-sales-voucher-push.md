# EXECUTION PLAN — F1b-1: Sales-voucher-out (strict mode, non-GST)

Status: **Backend + agent BUILT 2026-09-09** (serializer, push-readiness,
job lifecycle, result processor, admin + self-serve routes, finalize-time
auto-enqueue, .NET agent module — all live-verified against a real
TallyPrime gateway, both directly and through the actual shipped
serializer). 480 backend tests pass; `dotnet build` clean. **Frontend
(sync-status chip + push button) not started.** No full agent-process
live e2e run yet (build-verified + the exact wire payload proven
separately — see the agent section below). Second slice of **F1**
(`MASTER-tally-complement-saas.md` §F1), builds directly on F1a (deployed,
live-verified). Pushes a finalized invoice into the shop's Tally as a
`Sales` voucher, automatically, via the same agent/job transport F1a built
for the pull direction.

## Live probe findings — 2026-09-09 (read before touching the serializer)

Before writing code, the planned voucher shape was fired directly at the
dev box's live TallyPrime (`http://localhost:9000`, company "Fleek") via
raw `curl` — same technique F1a used to validate the Export/masters-pull
envelope. ~20 request variants, systematically narrowed. Four real
vouchers were created (`LASTVCHID` 1-4) and manually cleaned up afterward
(gateway `ACTION="Delete"` needs a `MASTERID`/`GUID`, not just
`VOUCHERNUMBER` — deletion by number 404s with "Cannot delete unnamed
object", so cleanup of throwaway push-test vouchers has to be done by hand
in the Tally UI until/unless a delete helper is built).

**Finding 1 — false lead, cost most of the probe time: TallyPrime
Educational/unlicensed mode restricts voucher **dates** to the 1st or 2nd
of the calendar month only.** Every voucher creation attempt dated
`2026-09-09` failed with the *identical* generic
`Voucher date is missing for: '<type>' voucher <num>. ... retry Split.`
error, regardless of voucher type (Sales/Purchase/Journal all failed the
same way), regardless of inventory vs. pure-accounting, regardless of
field set, envelope variant, or whitespace — while **master creation
(LEDGER/UNIT/STOCKITEM) worked perfectly the entire time** on the same
gateway/company. The error text is misleading (a generic edu-mode
date-validator message, not a real "field missing" complaint) and Tally's
own Sync Error Log (`Gateway of Tally` sync log view) only echoes the same
truncated per-field breakdown — no more detail available there. **Root
cause confirmed by direct test: `<DATE>20260902</DATE>` (the 2nd) created
successfully; `20260909` never did.** A licensed pilot shop's Tally will
not have this restriction — this is purely an artifact of testing against
an unlicensed/educational install. **Flag for any future session
debugging a "date is missing" LINEERROR against a dev/eval Tally: check
the date is the 1st or 2nd before chasing the XML.**

**Finding 2 — real, will affect the serializer: a standalone Round Off
ledger entry on an invoice-mode `Sales` voucher fails silently (no
`<LINEERROR>` text, just `EXCEPTIONS=1`).** Isolated cleanly across
several tests: it is not the ledger's name, not its parent group, not the
amount size (tested both `-0.20` and `-60.00`), not whether
`BILLALLOCATIONS.LIST` is present. It reproduces with *any* third ledger
occupying a `LEDGERENTRIES.LIST` slot that represents a **credit-side
adjustment distinct from the inventory item's own ledger** on this
install/voucher-mode. Two explicit `LEDGERENTRIES.LIST` lines work fine
when they're the *same side* (e.g. splitting the party's debit across two
lines). **Decided fix — proven working live:** don't emit a separate Round
Off ledger line at all. **Fold `invoice.round_off` into the inventory
line's own `AMOUNT` / `ACCOUNTINGALLOCATIONS.LIST` amount** (i.e., the
last real line's Sales-ledger amount = its `line_total` + the invoice's
total `round_off`), so the voucher has exactly: N inventory lines (their
`ACCOUNTINGALLOCATIONS.LIST` entries implicitly forming the credit side)
+ one party debit line, nothing else. This is arguably the more correct
representation anyway for a few-paise rounding adjustment — Sales stays
at (line totals + rounding), the party's net payable is what actually
matches the grand total, and Tally's own manual invoice entry doesn't put
Round Off as a separate line either for a sub-rupee adjustment. **The
"Round Off" section of "The voucher shape" below is updated accordingly —
there is no `<LEDGERENTRIES.LIST>` block for round-off in the final
design.**

**Confirmed working, unchanged from the original plan:** `Import Data`
request/`REQUESTDATA`/`TALLYMESSAGE` envelope exactly as drafted;
`VCHTYPE="Sales"`, `ACTION="Create"`; `ALLINVENTORYENTRIES.LIST` +
nested `ACCOUNTINGALLOCATIONS.LIST` per line; party debit via
`LEDGERENTRIES.LIST` with `BILLALLOCATIONS.LIST` (`BILLTYPE=New Ref`) —
bill-wise allocation on the party line was tested and works exactly as
designed, no changes needed there. **The one hard rule, unsurprisingly:
the voucher must net to exactly zero** — every failure that wasn't
Finding 1 or 2 traced back to an arithmetic mismatch introduced while
experimenting, not a Tally quirk. This is standard double-entry
validation, not something the serializer needs to work around beyond
"get the math right," which `domain.tax.compute_invoice` already
guarantees.

**Not yet live-tested:** the actual `<RESPONSE>` shape (`VOUCHERKEY`
presence — Open Question #1 below) was **not resolved** by this probe,
since none of the successful creates were inspected for a `VOUCHERKEY` in
the response body (the probe only checked `<CREATED>`); the
`<NARRATION>` weighment-summary field.

**Finding 3 — found while live-testing the actual serializer, not the
hand-written probe XML: the live HTTP POST path needs UTF-8, not
UTF-16.** `app/services/invoices/tally_xml.py` was built and its output
tested against Tally directly (same gateway, same company, same
"Fleek"/`ZZTEST *` fixtures as the manual probe above). Serializing with
`encoding="UTF-16"` (the purchase-side `inward/tally_xml.py`'s default)
got `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>` — a
different, earlier-stage failure than any seen during the manual probe
(Tally didn't even recognize it as a voucher-import attempt). The
byte-identical envelope re-serialized as UTF-8 was accepted immediately
(`CREATED=1`). **Root cause understood, not just worked around:**
`inward/tally_xml.py` defaults to UTF-16 because it writes a *file* the
accountant imports by hand — Tally reads a file's own declared encoding
fine. F1b-1's serializer output is POSTed live over HTTP instead, and
something in that transport path (likely curl's/httpx's raw-byte POST vs.
Tally's charset negotiation for an inline request body) doesn't handle a
UTF-16 body the same way. **Decided: `tally_xml.py` (sales) defaults to
`encoding="UTF-8"`**, documented in the function docstring so a future
session doesn't "fix" it back to UTF-16 by copying the purchase-side
convention. The .NET agent module sending this string over
`HttpClient`/`StringContent` should be UTF-8 too — flag this when
building the agent side (§B below expects `StringContent` in the
envelope's declared encoding; make that concrete: UTF-8).

**Live-verified, end to end, with the real serializer (not hand-written
probe XML):** `build_sales_voucher_xml_bytes()` on a fixture invoice (1
line, `line_total=960.00`, `round_off=0.20`, `grand_total=960.20`) →
`CREATED=1` on a real TallyPrime gateway. This is the first slice of F1
where the actual shipped module — not a probe standing in for it — was
proven against live Tally before any test-suite work started.

---

## Scope decision (locked)

**Strict mode.** A voucher is pushed only if every party/item on the invoice
already has a `tally_guid` (i.e., came from an F1a masters pull, or a prior
push already linked it). An invoice referencing a party/item Tally doesn't
know about is **not auto-created** in Tally — it's flagged, the push is
skipped, and the operator/accountant creates that master by hand (or the
firm re-runs a masters pull after adding it in Tally). Auto-push-missing-
masters is deferred to **F1b-2** — it roughly doubles serializer surface
(ledger create + stock-item create, both with their own name-matching
questions F1a already solved once for pull) and shouldn't gate proving the
voucher path itself against a real Tally.

**Non-GST only.** Invoices are `template_version = "v1-nongst"` today —
`subtotal → discount → round_off → grand_total`, no CGST/SGST/IGST split
(those columns exist on `Invoice`/`InvoiceLine` but are dormant, per the
model docstring — Phase 2). The voucher has exactly one tax-adjacent ledger
line: Round Off. When GST is turned on later, the serializer gains a branch
mirroring `app/services/inward/tally_xml.py`'s existing CGST/SGST/IGST
logic — not part of this slice.

**Manual re-push only.** No auto-retry loop beyond what the job/outbox
already gives for free (agent picks it up on next checkin, `tally_unavailable`
/`no_company_loaded` states retry). A failed or skipped push shows a button;
no background scheduler.

## Why this is smaller than it sounds

F1a already built the entire transport: `AgentOutboxItem` job queue,
`TallySyncJob` (already has `direction='out'` support in its shape),
`TallyGatewayClient.ExportAsync` (works unchanged for `Import Data` too —
same POST, different envelope), the checkin dispatch-once mechanic, the
`tally_reachable`/`tally_reason` health signal + pull-masters' existing
409-on-not-reachable gate, `TallyLink` (unused since F1a, built exactly for
this — `entity_type`, `tally_guid`, `checksum`, `last_pushed_at`), and the
Ops-console `TallyPanel` + `TallyConnectCard` shells. F1b-1 adds one new
serializer, one new job kind, one new agent action, and wires one hook.

Also **not from scratch**: `app/services/inward/tally_xml.py` (built for
the *purchase* side, F5) is a proven, tested pattern for exactly this shape
of problem — envelope construction, Decimal→string money formatting,
`ALLINVENTORYENTRIES.LIST` + `ACCOUNTINGALLOCATIONS.LIST` + bill-wise
`LEDGERENTRIES.LIST`, UDF tagging for our own reference id, UTF-16
serialization. F1b-1's serializer mirrors its structure, inverted for Sales
sign conventions, with the GST branch removed.

---

## The voucher shape (verified against `tally_xml.py`'s pattern)

```xml
<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME>
   <STATICVARIABLES><SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY></STATICVARIABLES>
  </REQUESTDESC>
  <REQUESTDATA>
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Sales" ACTION="Create">
     <DATE>{invoice.date YYYYMMDD}</DATE>
     <EFFECTIVEDATE>{same}</EFFECTIVEDATE>
     <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
     <VOUCHERNUMBER>{invoice.number}</VOUCHERNUMBER>
     <PARTYLEDGERNAME>{party.legal_name}</PARTYLEDGERNAME>
     <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
     <UDF:METALERP_REF>inv_{invoice.id}</UDF:METALERP_REF>

     <!-- one block per real line (description non-blank). Round-off is
          FOLDED into the LAST real line's amount (both the inventory
          AMOUNT and its ACCOUNTINGALLOCATIONS.LIST amount) rather than a
          separate ledger entry — see "Live probe findings", Finding 2.
          Every other line uses its plain line_total. -->
     <ALLINVENTORYENTRIES.LIST>
      <STOCKITEMNAME>{item.name}</STOCKITEMNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <RATE>{unit_rate}/{uom}</RATE>
      <AMOUNT>{line_total [+ round_off if this is the last real line]}</AMOUNT>
      <ACTUALQTY>{quantity} {uom}</ACTUALQTY>
      <BILLEDQTY>{quantity} {uom}</BILLEDQTY>
      <ACCOUNTINGALLOCATIONS.LIST>
       <LEDGERNAME>{ledger_map.sales_ledger}</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>{line_total [+ round_off if this is the last real line]}</AMOUNT>
      </ACCOUNTINGALLOCATIONS.LIST>
     </ALLINVENTORYENTRIES.LIST>
     <!-- ...repeated per real line... -->

     <!-- party debit = grand total (== sum of the above, round-off
          included), bill-wise New Ref. This is the ONLY LEDGERENTRIES.LIST
          block in the whole voucher — no standalone Round Off line. -->
     <LEDGERENTRIES.LIST>
      <LEDGERNAME>{party.legal_name}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>{-grand_total}</AMOUNT>
      <BILLALLOCATIONS.LIST>
       <NAME>{invoice.number}</NAME>
       <BILLTYPE>New Ref</BILLTYPE>
       <AMOUNT>{-grand_total}</AMOUNT>
      </BILLALLOCATIONS.LIST>
     </LEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>
```

**`ledger_map.round_off_ledger` is therefore unused by the serializer** —
it stays in the ledger-map schema (F1a already collects it, harmlessly)
but `push_blockers()` should **not** require it, only `sales_ledger`. Note
this narrows the "ledger_map_incomplete" blocker condition from the
original plan (§ Backend changes, item 2) — update that check when
building it.

**Sign convention, mirrored from the existing purchase voucher (opposite
polarity, same net-zero rule):** a Sales voucher **debits the party**
(`ISDEEMEDPOSITIVE=Yes`, negative bill-wise amount) and **credits Sales**
(`ISDEEMEDPOSITIVE=No`, positive line amount) — the exact inverse of
`tally_xml.py`'s Purchase voucher (which credits the party, debits
Purchase). **Live-verified 2026-09-09**: this sign pairing, the inventory
+ `ACCOUNTINGALLOCATIONS.LIST` structure, and the bill-wise
`BILLALLOCATIONS.LIST`/`New Ref` allocation all work exactly as designed
against a real TallyPrime gateway. The whole voucher must net to zero;
this is Tally's own double-entry validation, not ours — get the math
wrong and Tally's response (`EXCEPTIONS=1`, sometimes with a
`<LINEERROR>`, sometimes without — see Finding 2) says so, with nothing
written.

**`discount`** (both per-line ₹ and `invoice_discount`) is **not** a
separate ledger line — `tax.py`'s `line_total` and `subtotal` are already
net of it (see the totals-block fix memory: "subtotal is already net of
line discounts"). The voucher just uses the frozen `line_total` per line;
discount is invisible to Tally, same as it's invisible to the grand-total
arithmetic today. **Weighment** (F8): the segment/slip data does not appear
as extra inventory lines — Tally's inventory entries stay flat qty×rate per
item exactly as they are today; weighment context (which segment, recorded
scale weight) goes into voucher `<NARRATION>` as free text, not structured
fields. This was flagged as an open mapping decision in the F1a plan;
decided here.

---

## Data model — migration `0027` (verify `alembic heads` first)

No new tables — `TallySyncJob` and `TallyLink` already exist from F1a,
unused in the `out` direction until now.

- `tally_sync_job.kind` gains `'push_sales'` (already a free-text
  `String(20)`, no enum to alter).
- `tally_sync_job.direction = 'out'` (already supported).
- **New:** `tally_sync_job.entity_type` / `entity_id` (nullable
  `String(10)`/`String(36)`) — F1a's job rows are all `pull_masters` and
  don't reference a specific ERP record; a push job does (`entity_type=
  'invoice'`). Needed so the invoice-list sync chip can look up "the job for
  *this* invoice" without scanning `counts`/`error` JSON.
- `tally_link` needs no schema change — `entity_type='invoice'`,
  `entity_id=invoice.id`, `tally_guid` = the returned Tally `VOUCHERKEY` (or
  a locally-generated stable value if Tally's response omits it — see Open
  Questions), `checksum` = a hash of the pushed fields (grand_total +
  line count + line-total sum) so a re-push of an unchanged invoice is a
  no-op, `last_pushed_at` set on success.

```
0027_sales_voucher_push.py
  ALTER TABLE tally_sync_job ADD COLUMN entity_type VARCHAR(10) NULL
  ALTER TABLE tally_sync_job ADD COLUMN entity_id VARCHAR(36) NULL
  CREATE INDEX ix_tally_sync_job_entity ON tally_sync_job (entity_type, entity_id)
```

---

## Backend changes

### 1. Serializer — `api/app/services/invoices/tally_xml.py` (new)

Mirrors `app/services/inward/tally_xml.py` in structure (same `_sub()`
helper, same `_money`/`_neg`/`_yyyymmdd` pattern, same UDF-namespace
approach), inverted sign convention, GST branch removed:

```python
def build_sales_voucher_envelope(
    invoice: Invoice, party: Party, ledger_map: dict[str, str],
) -> etree._Element: ...

def build_sales_voucher_xml_bytes(
    invoice: Invoice, party: Party, ledger_map: dict[str, str],
    *, encoding: str = "UTF-16",
) -> bytes: ...
```

- **Precondition check lives in the caller, not here** — this module
  assumes every line's `item.tally_guid` is set and `ledger_map` has
  `sales_ledger`; it raises `ValueError` if missing (a programming error
  if reached, since the caller gates first). `round_off_ledger` is not
  read — round-off is folded into the last real line's amount, not a
  separate ledger line (live-verified; see "Live probe findings").
- Real lines only (`description.strip()` non-empty), same filter
  `finalize_invoice` already uses for `real_lines`.
- `STOCKITEMNAME` = `item.name` (the Tally-side name at time of push — if
  it drifted from what was pulled, Tally still resolves by name, not GUID,
  same as the purchase voucher does today).
- `NARRATION` (voucher-level, optional): if `invoice.weighment_slips` is
  non-empty, render a one-line summary ("Weighment: seg 1 = 487.50kg
  recorded") — best-effort, never blocks.
- Discount: no separate line (see "The voucher shape" above).

### 2. Push-readiness check — `api/app/services/tally/push_readiness.py` (new)

```python
@dataclass
class PushBlocker:
    code: str   # 'no_tally_company' | 'ledger_map_incomplete' |
                # 'party_not_linked' | 'item_not_linked' | 'no_lines'
    message: str

def push_blockers(session, invoice: Invoice) -> list[PushBlocker]:
    """Empty list = pushable. Never raises."""
```

- `no_tally_company` — tenant has no `tally_company` row, or it has no
  `shop_id` (agent not provisioned).
- `ledger_map_incomplete` — `sales_ledger` missing from
  `tally_company.ledger_map`. (`round_off_ledger` is **not** required —
  round-off is folded into the last line's amount, no separate ledger
  entry; `debtors_parent` is F1a's, already required there and not
  re-checked here.)
- `party_not_linked` — `invoice.party.tally_guid is None`.
- `item_not_linked` — any real line's `item.tally_guid is None` *or*
  `item_id is None` (a line whose item was never resolved at all — same
  precondition, worse case). Message lists the offending line
  descriptions (max 3, "+N more").
- Also (not a `PushBlocker`, a hard 409 the router raises directly, reusing
  F1a's existing helper): **not reachable right now** —
  `services/tally/agent_health.tally_reachable_now(shop) is False` with a
  fresh signal. A stale/unknown signal does **not** block (same "don't hard
  -block on staleness" rule the pull-masters gate already follows) — the
  agent will simply report `tally_unavailable` when it tries.

### 3. Job lifecycle — extend `api/app/services/tally/jobs.py`

- `enqueue_push_sales(session, invoice) -> TallySyncJob`:
  - Calls `push_blockers()` first; raises `HTTPException(422, ...)` joining
    all blocker messages if non-empty (mirrors `enqueue_pull_masters`'s
    422-on-no-shop shape).
  - 409 if a **non-terminal** push job already exists for this
    `(tenant_id, entity_type='invoice', entity_id=invoice.id)` — reuses the
    `_expire_stuck_pull`-style lazy-expire logic, generalized to
    `_expire_stuck_job(job)` (works for either direction, same 10-minute
    window + `last_agent_status` check).
  - Creates `TallySyncJob(direction='out', kind='push_sales',
    entity_type='invoice', entity_id=invoice.id, company_id=...)` +
    paired `AgentOutboxItem{module:"tally", payload:{job_id, action:
    "push_sales", company_name}}`.
  - **Idempotency at enqueue time, not just at Tally's door**: if
    `tally_link` already exists for this invoice with a `checksum` matching
    the invoice's *current* frozen totals, skip silently (return the
    existing link's info, no job created) — a re-finalize-triggered enqueue
    (shouldn't happen, invoices are immutable post-finalize, but defensive)
    or a manual "push again" click on an unchanged invoice is a no-op.
- `complete_push_ok(session, job, *, tally_guid, voucher_key)`: writes/
  updates the `TallyLink` row, `job.status='ok'`.
- `complete_push_error` reuses the existing `complete_job_error`.

### 4. Result processing — `api/app/services/tally/push.py` (new)

Mirrors `services/tally/pull.py::process_pull_result`:

```python
def process_push_result(session, job, *, ok, tally_response, agent_error):
    """Agent posted a result for a push_sales job. Parses Tally's XML
    response for <CREATED>1</CREATED> + a VOUCHERKEY, or a <LINEERROR>.
    Never raises — always leaves the job in a terminal or retry-safe state.
    """
```

- Unlike pull (which downloads XML from R2), **the agent sends Tally's
  response XML directly in the callback body** (it's small — a few KB, no
  R2 round-trip needed for a single-voucher response). New field on
  `JobResultIn`: `tally_response: str | None` (raw XML, capped ~16KB).
- Parse `<CREATED>` count and any `<LINEERROR>` text (reuse a small
  `lxml`-based extractor — the shape is the same
  `<RESPONSE>`/`<LINEERROR>` Tally already returns, seen live during F1a's
  masters-pull testing).
- `CREATED >= 1` → success. Tally's Import response does **not** reliably
  return a `VOUCHERKEY` in every version/config (open question below) — if
  present, store it in `tally_link.tally_guid`; if absent, store a
  deterministic fallback (`f"pushed:{invoice.number}"`) so the link row
  still exists for idempotency, flagged via a `tally_masterid=None`.
- `LINEERROR` present → `complete_job_error` with the raw error text
  (surfaced verbatim to the operator — these are usually specific: "Could
  not find stock item", "Voucher does not balance").

### 5. Router — extend `api/app/routers/tally.py`

```
POST /api/admin/firms/{firm_id}/tally/invoices/{invoice_id}/push
  -> enqueue_push_sales; 422 with blocker list; 409 in-flight/not-reachable
GET  /api/admin/firms/{firm_id}/tally/invoices/{invoice_id}/push-status
  -> latest job for this (tenant, invoice) + push_blockers() (so the UI can
     show "why not" even before a job exists)
```

Also add a **tenant-scoped** mirror in `tally_self_serve.py`
(`/api/tally/invoices/{invoice_id}/push`, `WriteUser`) — pushing on
finalize is automatic (see §6), but a manual re-push button lives on the
invoice detail page itself, which the firm's own users see, not just the
operator. Same push_blockers()/enqueue function, scoped to
`current_user.tenant_id`.

### 6. Finalize hook — `api/app/services/invoices/finalize.py` — BUILT

After `_render_pdf_best_effort` (the existing best-effort pattern —
finalize must never fail *because* of a Tally problem):

```python
_enqueue_tally_push_best_effort(session, invoice)
```

- No-ops silently (a debug-level log line, not spam) if `push_blockers()`
  is non-empty — this is the **expected common case** for any firm not yet
  on F1b (no `tally_company`, or masters not pulled yet). Only actually
  enqueues when every precondition already holds. Does **not** raise
  `HTTPException` the way the interactive push routes do (a 409
  "not-reachable"/"in-flight" from `assert_tally_reachable`/
  `assert_no_push_in_flight` is caught by the same broad
  `except Exception` and treated as "don't push this time" — finalize is
  not the place to surface "Tally is closed" to someone typing a bill).
- Wrapped in the same `try/except Exception: log + continue` shape as PDF
  rendering — a Tally-side failure must never block or roll back a
  finalize.
- This *is* F4 ("mobile billing syncs to Tally") — per the F1a plan's
  explicit call to fold F4 into F1b rather than build it standalone. No
  separate F4 slice.

**Real bug found + fixed while building this (2026-09-09): stale
`InvoiceLine.item` relationship inside the same finalize transaction.**
`push_blockers()` originally checked `ln.item.tally_guid` directly.
`InvoiceLine.item` is `viewonly=True, lazy="joined"` — loaded once when
`invoice.lines` was first fetched, *before* finalize's own item-resolution
step (§ finalize.py step 4) writes `line.item_id`. SQLAlchemy's identity
map does not retroactively refresh an already-loaded `viewonly` relationship
within the same session just because the FK column changed, so
`push_blockers()` running moments later — same session, same
transaction — saw `ln.item` as the value it had *before* resolution, not
after, and reported every line as unlinked even when the resolved item
genuinely had a `tally_guid`. Confirmed the mismatch directly: a
standalone `push_blockers()` call in a **fresh session** after finalize
returned `[]` (correct), while the in-process finalize-time call saw
`item_not_linked` (wrong) for the identical invoice. **Fix:**
`push_readiness.py`'s item-linked check now does
`session.get(Item, ln.item_id)` per line instead of trusting `ln.item` —
`session.get()` always reflects the identity map's current state. This
only bit the finalize-time (same-session) call; the manual push routes
(fresh request/session per call) never hit it, which is why it wasn't
caught until the finalize-hook tests specifically exercised the
"everything pre-linked, invoice created fresh, then finalized" path.
**Any future same-session reader of `InvoiceLine.item` right after a write
to `line.item_id` should assume the same trap applies.**

### 7. Invoice list/detail — sync status

- `InvoiceListItem` / invoice detail response gain `tally_sync_status:
  'not_linked' | 'pending' | 'synced' | 'error' | None` — derived from the
  latest `tally_sync_job` for `(entity_type='invoice', entity_id)`, same
  one-aggregate-join discipline the existing `payment_status` field uses
  (no N+1).
- `'not_linked'` (no `tally_company`, or blockers present) renders **no
  chip at all** by default — most invoices, most firms, forever, shouldn't
  show Tally UI noise. Chip only appears once the firm has a working Tally
  connection (`tally_company.shop_id` set).

---

## tally-agent (.NET) changes — BUILT + live-verified 2026-09-09

**Backend (enqueue):** `enqueue_push_sales` (`services/tally/jobs.py`) calls
`build_sales_voucher_xml_bytes()` at enqueue time and stores the UTF-8-
decoded string directly in `AgentOutboxItem.payload["voucher_xml"]` —
option (a), no schema change. Test coverage confirms the outbox payload
carries a well-formed `<?xml...` string with the right voucher type, party
name, and ledger name baked in. This was a real gap the first build pass
missed — `enqueue_push_sales` created the job and outbox item but never
actually built or attached the voucher, which would have left the agent
with `action: "push_sales"` and nothing to send. Caught before any
agent-side work started, not in production.

### `Modules/TallyMastersModule.cs` — extended, not forked

`RunOnceAsync` now iterates every `module == "tally"` outbox item and
dispatches on `action` (`pull_masters` → `ProcessPullAsync`, `push_sales` →
`ProcessPushAsync`, previously the loop filtered to `pull_masters` only).
`ProcessPullAsync` is the renamed, behaviourally-unchanged former
`ProcessOneAsync` (now returns `bool` so the round can pick an overall
module status across a mixed batch).

`ProcessPushAsync`:
- Reads `payload["voucher_xml"]` — plain string, built and populated by the
  backend (confirmed above); no base64, no second R2 round-trip.
- POSTs it via **`gateway.ImportAsync`** (the existing alias for
  `ExportAsync` — kept the original method name rather than renaming, per
  the plan's own note that a rename touches every reference; `ImportAsync`
  already existed as the semantically-clearer entry point for a write).
  `ExportAsync`/`ImportAsync` already construct the request with
  `Encoding.UTF8` explicitly — matches Finding 3 (the live HTTP path needs
  UTF-8, not the purchase side's UTF-16 file default) with no change
  needed there.
- `"Could not find Company"` in the response body → `no_company_loaded`
  ping, job left queued (reused verbatim from pull).
- `HttpRequestException`/`TaskCanceledException` (gateway unreachable) →
  `tally_unavailable` ping, job left queued.
- Otherwise (success, `LINEERROR`, or anything else) → the **whole response
  body goes back to the backend unparsed**, via
  `PostJobResultAsync(jobId, "ok", r2Key: null, error: null, ct,
  tallyResponse: body)`. The agent never interprets `<CREATED>`/
  `<LINEERROR>` itself — `services/tally/push.py::process_push_result`
  owns that; the agent's job is purely transport. (Note: a `LINEERROR`
  response still gets reported with outer `status: "ok"` — from the
  agent's perspective the *round-trip* to Tally succeeded; the backend is
  the one that reads the body and decides the *voucher* creation failed.)

### `Backend/BackendModels.cs` / `BackendClient.cs`

- `JobResultRequest` gained `[JsonPropertyName("tally_response")] string?
  TallyResponse`.
- `PostJobResultAsync` gained an optional trailing `string? tallyResponse
  = null` parameter (backward-compatible with every existing pull-path
  call site).
- No other agent-side change — the outbox/checkin/ping machinery is 100%
  reused.

**Verification:** `dotnet build TallyAgent.slnx` — 0 warnings, 0 errors.
No agent test project exists (confirmed absent, same verification level
every prior Tally-agent slice shipped at). **Live-verified beyond the
build**: the exact bytes `build_sales_voucher_xml_bytes()` produces (the
same function the backend now calls at enqueue time) were POSTed directly
at the dev box's live TallyPrime gateway and created a real voucher
(`CREATED=1`, `LASTVCHID=6`) — this is the payload the agent's
`ProcessPushAsync` forwards verbatim, so the transport path is proven even
though the compiled agent binary itself wasn't run end-to-end against a
live checkin this session (that full loop — Ops console click → real
agent process → real Tally → result back in the console — is the same
kind of live e2e the seamless-onboarding slice did for pull, and is worth
doing once for push before calling F1b-1 fully shipped).

---

## Frontend changes

### Ops console (`TallyPanel.tsx`)

Not much — the operator's job here was already done by F1a (link company,
map ledgers). One addition: the **ledger-map card's "F1b" tag becomes
load-bearing for `sales_ledger` only** — it goes from "optional, needed
later" to "required, blocks pushes" once this ships. (`round_off_ledger`
stays optional/unused — folded into the line amount instead, see "Live
probe findings".) Add a small inline note when `sales_ledger` is blank:
*"Fill this in to let this firm's invoices sync to Tally automatically."*

### Invoice detail page (`InvoiceEditorPage.tsx` or wherever finalized-view
lives)

- Sync status line, mirroring the PDF-status pattern already there: chip
  (`Synced ✓` / `Pending ⟳` / `Not synced — <reason>` / `Failed —
  <error> [Retry]`), only rendered when `tally_sync_status is not null`.
- "Push to Tally" button when status is `not_linked` **and**
  `push_blockers()` came back empty from the status-check endpoint (i.e.,
  push is possible but didn't happen automatically — covers the "firm
  connected Tally *after* this invoice was already finalized" case).
- Clicking blockers (when present) shows the specific reason inline — not
  a generic "can't sync", the actual "this party was created manually and
  isn't in Tally yet" text.

### Invoice list (`InvoiceListPage.tsx`)

- Small chip per row, same idiom as the existing `WhatsappBadge` column
  (`components/WhatsappStatus.tsx` — reuse its visual pattern, new
  component `TallySyncBadge`).

---

## Tests

Backend (`api/tests/test_invoice_tally_push.py` — new):
- `build_sales_voucher_xml_bytes`: golden-XML assertion against a fixture
  invoice (2 lines, a discount, a nonzero round-off) — element-by-element
  check of the sign convention (party debit negative, sales credit
  positive, bill-wise `New Ref`), matching the existing
  `test_inward_tally_xml.py`-style pattern if one exists (check first).
- `push_blockers()`: each blocker code triggers correctly in isolation
  (no company / no shop / no ledger_map / party unlinked / item unlinked);
  all-clear returns `[]`.
- `enqueue_push_sales`: 422 with the right blocker text; 409 while a push
  job is in flight; idempotent no-op on an unchanged already-linked
  invoice; a *changed* invoice (shouldn't be reachable since finalized
  invoices are immutable, but test the checksum-mismatch branch directly
  at the service layer).
- `process_push_result`: `<CREATED>1</CREATED>` → job ok, `tally_link`
  written; `<LINEERROR>...</LINEERROR>` → job error, raw text preserved;
  malformed response → job error, no crash.
- Finalize hook: an invoice for a Tally-linked party/items on a fully
  configured firm auto-enqueues a job on finalize; one missing precondition
  → finalize still succeeds, no job, no error surfaced to the finalize
  caller.
- Router: manual push 422/409/201 paths; tenant-scoped route can't push
  another tenant's invoice (404, existing `_owned_*` pattern).

.NET: no test project (matches F1a's own verification level — `dotnet
build` clean is the bar, per the seamless-onboarding slice's precedent).

---

## Open questions — resolve before/while building

1. ~~Does Tally's Import-Data response reliably include `VOUCHERKEY`?~~
   **RESOLVED 2026-09-09.** No — a real success response from this install
   is exactly:
   ```xml
   <RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
   <LASTVCHID>6</LASTVCHID><LASTMID>0</LASTMID><COMBINED>0</COMBINED>
   <IGNORED>0</IGNORED><ERRORS>0</ERRORS><CANCELLED>0</CANCELLED>
   <EXCEPTIONS>0</EXCEPTIONS></RESPONSE>
   ```
   No `VOUCHERKEY`, no `MASTERID` — `LASTVCHID` is what's actually there,
   and `_extract_voucher_key()` (already written defensively to check it)
   correctly resolves it (`"6"` for that response, verified by feeding the
   real body through the function directly). **`tally_link.tally_guid` for
   a pushed invoice is Tally's `LASTVCHID` in practice, not a
   `pushed:{number}` fallback** — the fallback path exists but shouldn't
   normally fire. Caveat: `LASTVCHID` is a per-company sequential internal
   voucher id, not a stable cross-system GUID the way `VOUCHERKEY` would
   have been — fine for this slice's idempotency/traceability purpose
   (it's still unique enough to prove "a voucher was created"), but **not**
   sufficient on its own to look the voucher back up robustly if Tally's
   internal numbering ever gets renumbered/compacted. Revisit if a future
   slice needs to reliably re-fetch or alter a previously-pushed voucher.
   Related, still genuinely open: **deleting a voucher via the gateway
   needs the real key, not `VOUCHERNUMBER`** (`ACTION="Delete"` keyed on
   number alone 404s with "Cannot delete unnamed object: VOUCHER!" —
   confirmed live while cleaning up probe test vouchers). Since
   `LASTVCHID` also isn't necessarily what `ACTION="Delete"` wants, a
   programmatic "retract a wrongly-pushed voucher" path is **not
   available** from what's been proven so far — flagged as a real gap for
   any correction/reversal flow, not solved by this slice.
2. **`STOCKITEMNAME` = name vs. GUID.** Tally's Import matches inventory
   entries by *name*, not GUID (confirmed by `tally_xml.py`'s existing
   Purchase-side behavior) — so if an item's name changed in Metal ERP
   after the pull that gave it a `tally_guid`, the push could silently
   create/miss a different Tally stock item than intended. Worth a
   `LINEERROR`-adjacent sanity check (does the pushed name still match
   `item.name` we have on file for that `tally_guid`?) — flag as a known
   gap for F1b-1, not necessarily fixed there.
3. **Company-open requirement for a *write*, not just a read.** F1a's pull
   tolerates "no company loaded" as a retry state indefinitely. For a
   push, is an indefinitely-queued sales voucher acceptable, or should a
   push have a tighter/different auto-cancel window than pull's 10
   minutes (an unpushed sale sitting for hours is a bigger practical
   problem than a delayed masters refresh)? Lean: keep the same
   `_expire_stuck_job` window for consistency in F1b-1; revisit if pilot
   feedback says otherwise.
4. **Multiple invoices queued while Tally is closed overnight — ordering.**
   Tally vouchers don't strictly require in-order import, but
   `VOUCHERNUMBER` gaps/duplicates are worth a sanity pass once more than
   one push is queued at a time (not exercisable until a real pilot has
   volume).

## Not in this slice

- Auto-create missing party/item masters at push time (F1b-2).
- GST voucher branch (waits on Phase 2 GST being turned on at all).
- Receipt voucher (payment push) — natural next slice once Sales is
  proven; reuses the same transport, a new serializer over the existing
  `Payment`/`PaymentAllocation` bill-wise model.
- Credit note / Payment / Contra / Journal / Stock Journal — see the
  wider-conversation discussion in this session; each is its own slice.
- Any scheduler-driven batch push — enqueue is finalize-triggered only.
- Converging `tally_company.ledger_map` (F1a/F1b, sales-out) with
  `tally_ledger_config` (F5, purchase-in) into one config surface — both
  continue to exist independently.

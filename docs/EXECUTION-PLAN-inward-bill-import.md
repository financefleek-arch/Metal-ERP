# Execution Plan — Inward Bill Import (PDF → Tally XML)

**Goal:** a feature-flagged module on the same `metal.fleekfinance.in` platform where an
accountant uploads a supplier's PDF invoice, the module extracts it, auto-matches or
auto-creates the supplier and every line against the tenant's masters, reconciles the
totals, and — on Approve — produces a **downloadable Tally Purchase-voucher XML** that the
accountant hand-imports (Gateway of Tally → Import Data → Vouchers).

**Explicitly out of scope:** live push into a running Tally, a connector agent, Tally LAN
connectivity, purchase→stock movement, 3-way match (PO + GRN). All are "Future" in
`EXTENSION-inward-bill-import.md`.

**Relationship to M1:** fully parallel, off the billing critical path. Shares auth, the
tenant model, `party` / `item` / `item_alias` / `hsn_code`, and `domain/normalize.py`. The
only hard shared dependency is `domain/normalize.py` + a small item-resolution service —
see §Shared prerequisites.

> This is the phase-by-phase build plan (tasks / exits / per-phase deliverable). The
> architecture, matching rules, XML shape, and schema rationale live in
> [`EXTENSION-inward-bill-import.md`](EXTENSION-inward-bill-import.md) — read that first.

---

## Screens shipping in this module

| Screen | Phase | Status |
|---|---|---|
| Nav item "Inward" (visible only when `tenant.ext_inward_import`) | X0 | ⬜ |
| **`InwardListPage`** (`/inward`) — upload drop-zone + bills table (status, confidence badges) | X4 | ⬜ |
| **`InwardReviewPage`** (`/inward/:id`) — PDF pane │ header form │ totals-check panel │ lines table with per-line match cell | X4 | ⬜ |
| **Finalized review view** — read-only after Approve, **Download Tally XML** button, "save as template?" prompt | X5 / X6 | ⬜ |
| **`InwardSettingsPage`** (`/inward/settings`) — the `tally_ledger_config` form | X6 | ⬜ |

No editor for `supplier_template` internals in this milestone — it's saved from a bill and
applied automatically; the settings page is only the ledger-name config.

---

## Shared prerequisites (before X1 — build once, shared with M1 §4/§6)

These are **not** the Item Catalogue UI. They are the normalization core that both the
sales-accretion path (M1 §6) and the purchase line-matcher (X3) call.

| Task | Detail | Size |
|---|---|---|
| `app/domain/normalize.py` | The pipeline: lowercase → trim/collapse whitespace → strip punctuation → apply the tenant's `synonym` map → keep token order. Pure, no I/O. Unit-tested against a table of messy real inputs. | 1–2 d |
| `app/services/item_resolution.py` | `resolve_item(session, tenant_id, description, hsn) -> ItemMatch` — runs steps 1–3 of the ladder (exact `name_normalized` → `item_alias` → `pg_trgm` fuzzy with HSN boost/tie-break) and returns `(item_id | None, method, confidence, candidates)`. Steps 4 (LLM) and 5 (stage-new) are layered on in X3. Shared by M1 finalize accretion and X3. | 2–3 d |
| `GET /api/reference/hsn?q=` | Searchable HSN lookup (code or description) over `hsn_code`. Trivial; needed by the review screen and by M1 §4-item. Add to the existing `reference` router. | 0.5 d |

**Exit:** `pytest tests/domain/test_normalize.py` and `tests/services/test_item_resolution.py`
green against real Postgres (trigram needs `pg_trgm`); `GET /api/reference/hsn?q=syrup` returns matches.

---

## X0. Schema + feature-flag plumbing + skeleton (0.5 wk)

| Task | Detail |
|---|---|
| Enums | `app/models/_mixins.py`: `ExtractionMethod` (`einvoice_qr \| template \| table \| vision_llm`), `MatchMethod` (`exact \| alias \| fuzzy \| llm \| new \| manual`), `InwardStatus` (`uploaded \| extracting \| needs_review \| approved \| rejected \| error`), `SupplyType` (`intra \| inter`). Native-string style, matching the existing enums. |
| Models | `app/models/inward.py`: `InwardBill`, `InwardBillLine`, `SupplierTemplate`, `TallyLedgerConfig`, `ExtractionRun`. Schema per `EXTENSION-inward-bill-import.md` → *Schema*. All `tenant_id`-scoped, `PkUuidMixin` + `TimestampMixin`. Money `NUMERIC(15,2)`, confidence `NUMERIC(4,3)`, `*_json` columns as the `_JSON` variant (JSONB on PG). |
| Job table | `app/models/job.py`: `Job(id, tenant_id, kind, payload_json, status, attempts, last_error, created_at, started_at, finished_at)` — a minimal Postgres-row work queue (no Redis). Only the batch-extraction path (X7) enqueues; X1–X6 run extraction synchronously. Registered on `Base.metadata` now so one migration covers it. |
| Tenant flag | Add `ext_inward_import: bool = mapped_column(Boolean, server_default=false(), nullable=False)` to `Tenant`. Surface it in `TenantOut` (read-only for now; toggled by a DB update / seed until an admin screen exists). |
| Migration | `alembic revision --autogenerate -m "inward bill import: tables + tenant flag + job queue"` → hand-review → `0003_*`. `alembic upgrade head`, `alembic check`, `alembic downgrade -1` all clean in CI (full chain against real PG16). |
| Router skeleton | `app/routers/inward.py` — `APIRouter(prefix="/api/inward-bills")`, every endpoint from `EXTENSION-inward-bill-import.md` → *API* stubbed to `501`. Mounted in `main.py`. A `require_inward` dependency: 404s the whole router if `tenant.ext_inward_import` is false. |
| PDF storage | Reuse the invoice-PDF bind-mounted volume; a `inward/` subdir. `source_pdf_path` / `tally_xml_path` hold relative paths. Same "swap to S3 later, callers unchanged" contract as M1 §0. |
| Nav gating (web) | `web/src/lib/types.ts`: add `ext_inward_import` to the `me`/tenant type. `Shell.tsx`: render the "Inward" nav item only when set. Route stubs `/inward`, `/inward/:id`, `/inward/settings` behind the same guard (redirect to `/` when off). |
| Feature-flag test | `tests/test_inward_flag.py`: flag off → every `/api/inward-bills*` route 404s; flag on → the skeleton routes reach their `501`. |

**Deliverable:** with the flag flipped on a test tenant, the "Inward" nav item appears, the
three routes render empty shells, and every API endpoint exists (501). Migration reversible.

**Exit:** CI green (ruff + mypy + full alembic up/check/down + pytest). Flag-off isolation proven.

---

## X1. Extractor — text path + reconciliation gate (1.5 wk)

| Task | Detail |
|---|---|
| Deps | Add `pdfplumber`, `pypdf` (or `pymupdf` — decide now; `pymupdf` also does X7's image render, so prefer it) to `pyproject.toml`. Confirm the wheels install in the api image. |
| `app/services/inward/extract_text.py` | `extract(pdf_path) -> RawExtraction` — `pdfplumber.extract_table()` with column boundaries from the header row x-positions; reads **cells not lines** to survive column-wrap (`Discoun\nt`, `11,689.3\n0`, `1,052.04 (\n9%)`). Header + totals via labelled-field regex. Returns header fields, a line array, totals, and `raw_text`. No LLM. |
| `app/services/inward/einvoice_qr.py` | If a signed-QR (JWT) is present in the PDF, decode it — supplier/buyer GSTIN, doc no/date, total, HSN summary, IRN. Money fields then come from the QR; the line-level text parse still runs for descriptions/qty. `extraction_method = einvoice_qr`, highest confidence. |
| Text-layer detection | Per-page char count below a threshold → mark `needs_image_path` on the extraction. In X1 the image path returns `"unsupported"` and the bill lands `needs_review` with that reason (real image handling is X7). |
| `app/services/inward/reconcile.py` | `taxable_total + cgst_total + sgst_total + igst_total + round_off`, rounded 2dp, must equal `grand_total` (±0.05). Mismatch → `reconciled = false`, discrepancy recorded. **Nothing downstream generates XML unless reconciled.** |
| `app/services/inward/extraction_run.py` | Log every attempt to `extraction_run` (attempt #, method, ok, confidence, error, `llm_tokens` null here). |
| Orchestrator | `app/services/inward/run_extraction.py` — `POST /api/inward-bills` (multipart) → create `InwardBill` (`uploaded`) → **synchronously** for a single small PDF: QR? → template? (X6, skipped now) → table-extract → reconcile → set header/lines/totals, `extraction_method`, `extraction_confidence`, `status` (`needs_review` if any step low-confidence or unreconciled, else stays pending resolution in X2). |
| Wire real endpoints | `POST /api/inward-bills`, `GET /api/inward-bills`, `GET /api/inward-bills/{id}`, `GET /api/inward-bills/{id}/pdf` (streams the source PDF). |
| Tests | `tests/services/inward/` — 4–5 real supplier PDFs committed as fixtures incl. the Sugal Foods sample (`INV2526-5667`, 12 lines all HSN `21069092`, CGST9+SGST9, round-off 0.14, grand 42,445.00). Assert: line count, wrapped-cell values, header fields, totals, reconciliation pass/fail. A deliberately-broken-totals fixture → `needs_review`. |

**Deliverable:** `POST` a text PDF → `GET /api/inward-bills/{id}` returns extracted header +
12 lines + totals + `reconciled: true`; a scanned PDF returns `needs_review` /
`image path unsupported`.

**Exit:** `pytest tests/services/inward` green; the Sugal Foods sample reconciles to the paise.

---

## X2. Supplier resolution (0.5 wk)

| Task | Detail |
|---|---|
| `app/services/inward/resolve_supplier.py` | 1) **GSTIN exact** on `party.gstin` where `role in (supplier, both)` for the tenant → link (`matched_party_id`); a matched `customer` is staged for promotion to `both` on approve. 2) **No GSTIN on the PDF** → normalized-name trigram against supplier parties; a single hit > 0.85 → *propose* (never auto-link). 3) **No match** → build `new_supplier_staged_json`: `legal_name` (header), `gstin`, `pan` (GSTIN chars 3–12 if it passes `PAN_RE`), `default_state_code` (prefix), `role = supplier`, one `party_address` (type `both`) from the header block. Not written until Approve. |
| `supply_type` | Derived: supplier prefix == buyer (tenant) prefix → `intra` (CGST+SGST) else `inter` (IGST). `place_of_supply_state_code` from the PDF, defaulting to the supplier prefix. |
| Hook into orchestrator | `run_extraction` calls `resolve_supplier` after reconcile; result stored on the bill. |
| Tests | GSTIN hit → link; `customer` role → promotion staged; no GSTIN → trigram proposal, not linked; no match → staged party JSON has PAN + state derived correctly; inter-state detection from mismatched prefixes. |

**Deliverable:** `GET /api/inward-bills/{id}` shows `supplier: matched to <party>` or
`supplier: NEW — will be created` with the staged fields, plus `supply_type`.

**Exit:** all supplier-resolution tests green.

---

## X3. Line resolution ladder + shared `llm` module (1 wk)

| Task | Detail |
|---|---|
| Extend `item_resolution.py` | Add step 4 (LLM disambiguation) and step 5 (stage-new) on top of the X-prereq exact/alias/fuzzy core. Step 3 detail: `pg_trgm similarity() ≥ 0.55` over `name_normalized` + aliases; **+0.15** same `hsn_code`, **−0.10** different; take the top only if adjusted ≥ 0.72 **and** it beats the runner-up by ≥ 0.10. |
| `app/services/llm/__init__.py` | The shared LLM service: prompt templates + the shared Claude client (platform default model/key, same as the rest of the stack) + token logging into `extraction_run`. **X3 ships this as a real module** (stub-capable via a config flag so fuzzy-only mode works — see *Decision 1*). Nothing else in the module talks to an LLM. |
| Step 4 — disambiguation | **Ambiguous / weak** (multiple close candidates, or top < 0.72) → **one batched call per bill** covering all uncertain lines: each line description + that tenant's top-5 candidates (name, HSN, last rate) → returns an item id or `NONE` per line. Never invents an item. Confidence capped 0.80, `match_method = llm`, badged "AI-matched" in the UI. |
| Step 5 — stage new | `NONE` / no candidates → `new_item_staged_json`: `name` = description as-typed, `name_normalized` computed, `hsn_code` = the line's HSN **if it exists in `hsn_code`** (else null + `review_flag = 'unknown_hsn'`), `uom` from the PDF Units, `item_type` = `mrp` if UOM ∈ {Nos, Pcs, Set} else `bulk`, `source = auto_from_purchase`, `status = unconfirmed`. Written on Approve. |
| `review_flag` | Set per line: `unknown_hsn`, `low_confidence` (fuzzy < 0.72 and no LLM pick), `ambiguous` (LLM was consulted), `new`. Drives the red chips in the review UI. |
| Hook into orchestrator | `run_extraction` resolves every line after supplier resolution; per-line `match_method` / `match_confidence` / `matched_item_id` / `new_item_staged_json` / `review_flag` persisted. |
| Tests | exact hit; alias hit; fuzzy win with HSN boost; HSN tie-break flips the winner; ambiguous → LLM path (LLM client mocked) picks a candidate; LLM returns NONE → staged new item; unknown HSN → null + flag; UOM → item_type mapping. |

**Deliverable:** `GET /api/inward-bills/{id}` returns every line with a resolved match state
(`exact` / `alias` / `fuzzy NN%` / `AI-matched` / `NEW`) and a `review_flag` where relevant.

**Exit:** ladder tests green; fuzzy-only mode (LLM disabled by config) still produces a
complete resolution with `low_confidence` flags instead of `llm` matches.

---

## X4. Review UI — list + review page (1.5 wk)

| Task | Detail |
|---|---|
| `web/src/pages/inward/InwardListPage.tsx` (`/inward`) | Multi-file upload drop-zone at top (`POST /api/inward-bills`, one row per file, optimistic `extracting`). Table: filename, supplier, bill no/date, amount, status badge, confidence. Filters: status, supplier, date range. Row → review. TanStack Query, polls while any row is `extracting`. |
| `web/src/pages/inward/InwardReviewPage.tsx` (`/inward/:id`) | Split view. **Left:** the source PDF via `<embed>` / pdf.js (`GET .../pdf`). **Right, top→bottom:** (a) **Supplier block** — "matched to `<party>`" ↔ "NEW — will be created" toggle, editable bill no / date / place of supply, confidence chip; (b) **Totals-check panel** — taxable / CGST / SGST / IGST / round-off / grand, **green when reconciled**, red with the discrepancy when not; (c) **Lines table** — per row: description, HSN, qty, rate, amount, and a **match cell**: `→ SS Utensil (92%)` / `→ NEW item` / an inline search-combobox to override, plus the `review_flag` chip. |
| Match-cell combobox | Reuses the M1 `ItemCombobox` if it exists by now; otherwise a local debounced `/api/items` search (read-only endpoint — items list is M1 §4, but a minimal `GET /api/items?q=` may need to land here if M1 hasn't shipped it — coordinate). |
| `PATCH /api/inward-bills/{id}` wiring | Reviewer edits: correct header fields, override a line's `matched_item_id` (→ `match_method = manual`), switch the staged supplier to an existing party or vice-versa, clear/set line flags. Optimistic updates, last-write-wins. |
| Action bar | **Re-extract** (`POST .../re-extract`), **Reject** (`POST .../reject` + reason), **Approve** — Approve disabled with a reason list until: reconciled **and** every line has a resolution (matched or explicitly staged-new) **and** supplier resolved. |
| `GET`/`PATCH` response models | Pydantic schemas for the full review payload (header, lines with match state, per-field confidence, reconciliation, resolved/staged supplier). |
| Tests | API: `PATCH` overrides a line match; `PATCH` flips supplier; Approve-gate returns the blocking reasons. Web: component test that a red totals panel disables Approve. |

**Deliverable:** upload a PDF on `/inward`, open it, see the PDF beside the extracted data,
override one line's match, see the totals panel green, Approve enabled.

**Exit:** the review screen matches the `InwardReview.dc.html` mockup; Approve-gate correct.

---

## X5. Approve transaction + Tally Purchase-voucher XML (1 wk)

| Task | Detail |
|---|---|
| `POST /api/inward-bills/{id}/approve` | One SQLAlchemy transaction: (1) re-validate reconciliation + every line resolved + supplier resolved (422 otherwise). (2) Create the staged **new supplier party** (`role = supplier` or promote `customer`→`both`) + its address, if staged. (3) Create the staged **new items** (`source = auto_from_purchase`, `status = unconfirmed`, uom, `hsn_code`), normalized-key dedupe (a race that finds an existing normalized name links instead of creating). (4) Link every `inward_bill_line.matched_item_id`; bump `last_purchase_rate` / `last_purchased_at` (add these two columns to `item` in the X0 migration — dormant until now). (5) Build the XML → write to the volume → set `tally_xml_path`. (6) `status = approved`. (7) `audit_log`. |
| `app/models` addition | `item.last_purchase_rate NUMERIC(15,2)`, `item.last_purchased_at TIMESTAMPTZ` — fold into the X0 migration so X5 needs no new migration (or a small `0004` if X0 already shipped). |
| `TallyLedgerConfig` | `GET/PUT /api/inward-bills/settings/ledgers`. Defaults: `creditors_group='Sundry Creditors'`, `purchase_ledger='Purchase Accounts'`, `cgst_ledger='CGST'`, `sgst_ledger='SGST'`, `igst_ledger='IGST'`, `round_off_ledger='Round Off'`, `xml_encoding='UTF-16'`. Row auto-created with defaults on first read. |
| `app/services/inward/tally_xml.py` | Build the `<ENVELOPE>` per `EXTENSION-inward-bill-import.md` → *Tally Purchase voucher XML*: master-create `<LEDGER>` / `<STOCKITEM>` messages **only for NEW masters**, then the `<VOUCHER VCHTYPE="Purchase">` with `<DATE>`/`<REFERENCE>`/`<REFERENCEDATE>`/`<PARTYLEDGERNAME>`/`<VOUCHERNUMBER>`/`<PLACEOFSUPPLY>`, `UDF:METALERP_REF` = the `inward_bill` id, `<ALLINVENTORYENTRIES.LIST>` (one per line: stockitem, qty, rate, amount, per-line purchase/CGST/SGST allocations), `<LEDGERENTRIES.LIST>` (Purchase A/c taxable, CGST, SGST *or* IGST, Round Off, party credit = grand total). Dates `YYYYMMDD`. Intra/inter from `supply_type`. Serialize in the configured encoding with the matching `<?xml?>` declaration. |
| `GET /api/inward-bills/{id}/xml` | Stream the file (`approved` only; 409 otherwise). Batch download zips. |
| Real-Tally validation | **Blocking check-off:** import the generated XML into a real Tally Prime — the Sugal Foods sample must book a Purchase voucher with the party ledger, both new stock items, and the CGST/SGST/round-off allocations, no `<LINEERROR>`. Adjust encoding / tag order / ledger-parent until clean. Record the working Tally version in `DEPLOY-AND-OPS.md`. |
| Finalized view (web) | On Approve success the review page flips **read-only**: assigned nothing (Tally owns the number), frozen data, a **Download Tally XML** button. |
| Tests | Approve creates exactly the staged masters and no more; re-approve is idempotent (409); XML snapshot test for the sample (golden file, normalized for the UDF id); intra vs inter produces CGST+SGST vs IGST; unreconciled bill → 422. |

**Deliverable:** Approve the Sugal Foods bill → supplier + 2 new items created UNCONFIRMED,
lines linked, **Download Tally XML** live → the file imports into Tally with no error.

**Exit:** golden-file XML test green; one real Tally import verified and documented.

---

## X6. `supplier_template` learn / apply + settings page (0.5 wk)

| Task | Detail |
|---|---|
| `POST /api/inward-bills/{id}/save-template` | Persist a `supplier_template` from an approved bill: `supplier_gstin`, `column_ranges_json` (the x-ranges the table extractor used), `header_anchors_json`, `uom_map_json`, `default_purchase_ledger` / `default_cgst_ledger` / `default_sgst_ledger` / `default_igst_ledger`. `UNIQUE(tenant_id, supplier_gstin)` — re-save updates. |
| Apply on extract | `run_extraction`: after QR, before generic table-extract, look up `supplier_template` by GSTIN → if found, apply its column ranges + anchors + UOM map, skip ladder steps 3–5 and the LLM, band confidence high. |
| "Save as template?" prompt (web) | On the first approved bill from a GSTIN with no template, the finalized view shows an opt-in prompt → calls `save-template`. |
| `POST /api/inward-bills/{id}/re-extract` | Re-run extraction from `raw_text` / the PDF (e.g. after a template is saved, or a method override). Bumps `extraction_run.attempt`. |
| `web/src/pages/inward/InwardSettingsPage.tsx` (`/inward/settings`) | The `tally_ledger_config` form — six ledger-name fields + the encoding toggle. Small. `GET/PUT .../settings/ledgers`. |
| Tests | Save template from an approved bill; the tenant's next bill from the same GSTIN extracts via the template (no LLM call — assert the mock client was not invoked) at high confidence; re-extract bumps the attempt counter. |

**Deliverable:** approve a second Sugal Foods bill → it extracts through the saved template,
no LLM, lands ready-to-approve with green totals; `/inward/settings` edits the ledger names.

**Exit:** template apply-path test green (LLM client asserted unused).

---

## X7. Extractor — image path + batch upload + hardening (0.5 wk)

| Task | Detail |
|---|---|
| Deps | `pymupdf` page render (already added in X1 if chosen there). |
| `app/services/inward/extract_image.py` | Sparse/no text layer → render each page to a PNG (`pymupdf`, ~150 dpi) → **one vision-LLM call** through the X3 `llm` module (reuses its client + token logging): page image(s) + the target JSON schema (header, line array, totals) → structured output. `extraction_method = vision_llm`, confidence banded one notch below the text path (so an image bill effectively always → `needs_review`). Reconciliation gate still applies. Page images kept beside the PDF for the review pane. |
| Review pane (web) | When `extraction_method = vision_llm`, the left pane shows the rendered page images (already stored) rather than / alongside the PDF embed. |
| Batch upload → `job` | `POST /api/inward-bills` with many files → one `Job(kind='inward_extract')` per file, `status = extracting` immediately; a lightweight worker loop (a FastAPI startup task or a separate `python -m app.worker` process — decide per deploy) drains the queue. Single small PDFs still run synchronously. |
| Per-tenant page-render cap | Soft-warn / hard-stop monthly vision-page counter (see *Decision 5*) — a simple counter on `tenant` or a `usage` row, checked before an image call. |
| Hardening | Corrupt/encrypted PDF → `status = error` with a message, never a 500. `extraction_run` captures the traceback. Size limit on upload. Docs: a section in `DEPLOY-AND-OPS.md` (the volume, the worker, the LLM key, the cap, how to re-extract). |
| Tests | A scanned fixture → `vision_llm` path (LLM client mocked to return valid structured JSON) → reconciles → `needs_review`; batch of 3 → 3 jobs → all drained; cap hit → hard stop with a clear error. |

**Deliverable:** upload a scanned invoice → it extracts via the vision call, lands in review
with page images shown; upload 5 at once → all process via the job queue.

**Exit:** image-path test green; batch drains; cap enforced; `DEPLOY-AND-OPS.md` updated.

---

## Timeline

| Phase | Duration | Notes |
|---|---|---|
| Shared prereqs (`normalize.py` + `item_resolution` core + HSN endpoint) | 0.5 wk | shared with M1 §4/§6 — build once, whoever reaches it first |
| X0 Schema + flag + skeleton | 0.5 wk | migration `0003`, router stubs, nav gating |
| X1 Text extractor + QR + reconcile | 1.5 wk | pdfplumber cell-reading, 5 real fixtures |
| X2 Supplier resolution | 0.5 wk | GSTIN key, stage-new, intra/inter |
| X3 Line ladder + `llm` module | 1 wk | shared `llm` service lands here |
| X4 Review UI | 1.5 wk | list + split review page + PATCH |
| X5 Approve txn + Tally XML | 1 wk | **real Tally import is a blocking check** |
| X6 `supplier_template` + settings | 0.5 wk | learn/apply per GSTIN |
| X7 Image path + batch + hardening | 0.5 wk | vision-LLM, `job` queue, usage cap |
| **Total** | **~7.5 wk** | one dev, fully parallel to the M1 billing critical path |

**Two-track note:** if a second dev takes this while the first drives M1, the only sync
point is the shared prereqs — agree the `ItemMatch` shape and `normalize.py` signature up
front, then the tracks are independent until X4 (where a minimal `GET /api/items?q=` must
exist — land it in whichever track reaches it first).

---

## Decisions to confirm (from `EXTENSION-inward-bill-import.md`)

1. **LLM in X3 from the start, or fuzzy-only first?** Recommend: ship the `llm` module in
   X3 but default it **off** by config; turn it on once real bills show the fuzzy miss
   rate. X7's image path needs the module regardless, so it lands either way.
2. **`tally_ledger_config` defaults** — confirm `Purchase Accounts` / `CGST` / `SGST` /
   `Round Off` match the shop's chart of accounts.
3. **XML encoding** — UTF-16 vs UTF-8; settle it against the real Tally version during X5.
4. **Batch volume per tenant per day** — sets whether X7's job queue is load-bearing or a
   nicety.
5. **Vision-LLM cost ceiling** — decide the soft-warn / hard-stop monthly page numbers
   before X7.

---

## Critical path & risks

1. **X1 cell-reading fidelity** — the column-wrap hazard (`11,689.3\n0`) is the classic
   time-sink. Timebox against the 5 real fixtures; get "correct on our suppliers", not "any
   PDF on earth".
2. **X5 Tally import pickiness** — encoding, tag order, ledger parents. Validate against a
   live import **in X5**, not at go-live. This is the single highest-risk item.
3. **`pg_trgm` must be present** — same shared-Postgres note as M1; the `metalerp` DB is
   created with the extension (`0002`).
4. **LLM determinism in tests** — always mock the client in unit tests; the one real
   round-trip check is manual, in X5.
5. **Feature-flag isolation** — X0's flag-off test must stay green forever; the module must
   never leak routes or nav to a tenant without `ext_inward_import`.
6. **Idempotency** — `UDF:METALERP_REF` = the `inward_bill` id + Tally's `VOUCHERNUMBER`
   dup-check are the two guards against a double re-import. Test re-approve → 409.

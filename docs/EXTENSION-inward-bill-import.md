# Extension — Inward Bill Import (PDF → Tally XML)

**A pluggable module** on the same `metal.fleekfinance.in` platform. A user
logs in, uploads a supplier's PDF invoice, the module extracts it,
**auto-creates or matches the supplier and the items** against the
tenant's existing masters, and produces a **downloadable Tally Purchase
voucher XML** the accountant imports by hand (Gateway of Tally → Import).

No live push, no connector agent, no Tally connectivity for this scope.
Live push is a later, separate increment (see *Future* at the end).

---

## Why "pluggable"

The core ERP (M1) is invoicing. Inward-bill import is a **self-contained
feature** that:

- shares the platform's auth, tenant model, `party` and `item` masters,
  `hsn_code` reference, and the `domain/normalize.py` pipeline;
- adds its own tables (`inward_bill*`, `supplier_template`, `extraction_run`),
  its own router (`/api/inward-bills`), its own React route (`/inward`);
- is gated by a per-tenant flag `tenant.ext_inward_import DEFAULT false`
  (nav item and routes hidden when off);
- can be built, deployed, and priced independently of the billing roadmap.

It is **not** a new app — same repo, same container, same DB, same deploy
pipeline. "Pluggable" = one feature flag + an isolated code surface, not a
microservice.

---

## User flow

```
/inward  (nav item, visible when tenant.ext_inward_import = true)
  │
  ├─ Upload — drag a PDF (or several). Each → an `inward_bill` row, status UPLOADED.
  │
  ├─ Extraction runs (sync for a single small PDF; a Postgres `job` for a batch):
  │     1. pull text (pdfplumber / pymupdf).
  │     2. HAS a usable text layer:
  │          a. GST e-invoice signed-QR present → decode → near-perfect, skip the rest.
  │          b. else supplier_template for this GSTIN → apply it.
  │          c. else table-extract the line grid + regex the header/totals.
  │     3. NO / sparse text layer (scanned or image-only) → render each page to
  │        an image → one VISION-LLM call: page image(s) + the target JSON schema
  │        → structured header + lines + totals.  (Same LLM the line-matcher uses;
  │        no separate OCR engine.)  Confidence banded one notch below the text path.
  │     4. reconcile: taxable + CGST + SGST + IGST + round-off == grand total (±0.05).
  │        If any step is low-confidence OR reconciliation fails → status NEEDS_REVIEW.
  │
  ├─ Supplier resolution (see "Party matching" below):
  │     GSTIN match → link to existing party.  No match → stage a NEW supplier party.
  │
  ├─ Line resolution (see "Item matching" below):
  │     per line: exact-normalized → alias → trigram fuzzy (+HSN tie-break) → LLM → NEW item.
  │
  ├─ Review screen — PDF on the left, editable extracted fields on the right:
  │     • confidence badges per field; red on anything below threshold or unreconciled
  │     • supplier: "matched to <party>" or "NEW — will be created" (editable)
  │     • each line: "matched to <item> (92%)" / "NEW" / a picker to override
  │     • totals check panel (must be green to approve)
  │
  └─ Approve →
        • create the NEW supplier party if staged (role = supplier, status = active,
            source = inward_bill, source_ref = <inward_bill.id>);  a matched customer → both
        • set party.last_txn_at = bill_date (forward-only), for matched or created
        • create the NEW items (source = AUTO_FROM_PURCHASE, status = UNCONFIRMED)
        • link every line to its item_id; bump last_purchase_rate / last_purchased_at
        • build the Tally Purchase VOUCHER XML
        • status → APPROVED;  a Download XML button appears
        • (first bill from a new supplier) → "Save as template for future <supplier> bills?"
```

The accountant then, in Tally: **Gateway of Tally → Import Data → Vouchers
→ pick the downloaded `.xml`**. Tally books the purchase voucher (and any
`<LEDGER>` / `<STOCKITEM>` master-create messages bundled ahead of it).

---

## Party matching — supplier

Primary key is the **supplier GSTIN** (15 chars, validated by
`app/reference.py`'s `GSTIN_RE`). On every inward bill:

1. **GSTIN exact match** against `party.gstin` where `role in (supplier, both)`
   for this tenant → link. (If the matched party is `role = customer`,
   promote it to `both`.)
2. **No GSTIN on the PDF** (rare for a tax invoice) → fall back to a
   normalized-name trigram match against supplier parties; if a single
   strong hit (> 0.85) → propose it in the review screen, never
   auto-link silently.
3. **No match** → stage a **new** `party`:
   - `legal_name` = supplier name from the PDF header
   - `gstin` = extracted GSTIN
   - `pan` = derived from the GSTIN (chars 3–12) if it passes `PAN_RE`
   - `default_state_code` = GSTIN prefix (chars 1–2)
   - `role` = `supplier`, `status` = `active`
   - one `party_address` (type `both`) from the header address block
   - created only on **Approve**, so a rejected bill leaves no party behind.

**Provenance (columns shipped in migration `0003`).** On Approve, a
module-created supplier is written with `source = inward_bill` and
`source_ref = <inward_bill.id>` (deep-links the Parties page back to the
bill). A *matched* `customer` promoted to `both` keeps its original
`source` untouched — the promotion doesn't rewrite where the party came
from. Either way, `party.last_txn_at` is bumped to the bill date
(forward-only — never moved backwards by an older bill); inward-approve
is the second writer of that column alongside invoice-finalize.

`party.tally_guid` stays null for module-created suppliers — it's only set
by the Tally *import* path. The XML's `<LEDGER>` create message uses the
`legal_name`; the accountant's Tally matches by name on import.

---

## Item matching — inward line → `item`

Each PDF line has: description, HSN, qty, UOM, rate. Resolution ladder,
stop at the first hit:

| Step | Rule | Confidence |
|---|---|---|
| 1 | `normalize(description)` **exact** == an `item.name_normalized` (tenant-scoped) | 1.00 |
| 2 | `normalize(description)` matches an `item_alias.alias_normalized` | 0.98 |
| 3 | **Trigram fuzzy** (`pg_trgm`, `similarity() ≥ 0.55`) over `item.name_normalized` + aliases, **HSN as a tie-break / boost**: candidates with the same `hsn_code` get +0.15, a different `hsn_code` gets −0.10. Take the top candidate if its adjusted score ≥ 0.72 **and** it beats the runner-up by ≥ 0.10. | score |
| 4 | **Ambiguous or weak** (multiple close candidates, or top < 0.72) → **LLM disambiguation**: send the line description + the top 5 candidates (name, HSN, last rate) + "which is the same product, or NONE" → returns an id or `none`. | LLM-assigned (capped at 0.80) |
| 5 | **NONE / no candidates** → stage a **new** `item`: `name` = description as-typed, `name_normalized` computed, `hsn_code` = the line's HSN if it exists in `hsn_code` (else null + a review flag), `uom` mapped from the PDF's Units, `item_type` = MRP if UOM is Nos/Pcs/Set else BULK, `source = AUTO_FROM_PURCHASE`, `status = UNCONFIRMED`. Created on **Approve**. | — |

**HSN is a strong signal, not a key** — many distinct products share one
HSN (all 12 lines in the sample invoice are `21069092`). So HSN never
matches on its own; it only re-ranks the fuzzy candidates and gates
new-item creation (a line whose HSN isn't in the reference list gets
flagged for the reviewer).

**LLM usage** — only step 4, only when fuzzy is ambiguous. Batched: one
call per bill covering all its uncertain lines, not one call per line.
The prompt gets *candidates from this tenant's catalogue only*; the model
picks or says none — it never invents an item. Every LLM-assigned match
still shows in the review screen with an "AI-matched" badge and can be
overridden. Model + provider follow the platform default (Claude via the
shared key), same as elsewhere in the stack.

Newly-created purchase items flow into the same **catalogue accretion**
story as sales-created ones — they show up in the Items page as
`UNCONFIRMED`, get merged/confirmed with the existing tooling, and (Stage
1+) their purchase name learns as an alias so the next bill from that
supplier matches at step 2.

---

## Extraction detail

**Text invoices (the common case — the sample PDF is one).**
`pdfplumber.extract_table()` with column boundaries derived from the
header row x-positions. Handles the column-wrap hazard (`Discoun\nt`,
`11,689.3\n0`, `1,052.04 (\n9%)`) by reading cells, not lines. Header and
totals via labelled-field regex. No LLM.

**Scanned / image-only invoices.** `pdfplumber` returns little or no
text (below a per-page char threshold) → render each page to a PNG
(`pymupdf`, ~150 dpi) → **one vision-LLM call**: the page image(s) plus
the target JSON schema (header fields, line array, totals) → structured
output. This is the same LLM/provider the line-matcher uses (Claude via
the shared key) — no Tesseract, no separate OCR service, one dependency.
Confidence is banded one notch below the text path, so an
image-extracted bill effectively always lands in `NEEDS_REVIEW`; the
reconciliation gate still applies. The rendered page images are kept
alongside the PDF for the review screen.

**GST e-invoices.** If the PDF carries the signed-QR (JWT), decode it —
it contains supplier/buyer GSTIN, doc no/date, total, HSN summary, and
the IRN. Highest confidence; the line-level parse still runs for
descriptions/qty but the money fields come from the QR.

**One vision-LLM shape, two uses.** The extractor's image path and the
line-matcher's disambiguation call share a small `llm` service module
(prompt templates + the shared client + token logging into
`extraction_run`). Nothing else in the module talks to an LLM.

**Reconciliation gate** (always, regardless of method):
`taxable_total + cgst_total + sgst_total + igst_total + round_off`
rounded to 2dp must equal `grand_total`. Mismatch → `NEEDS_REVIEW` with
the discrepancy shown. Nothing generates XML until totals reconcile.

**`supplier_template`** — saved on first successful approve of a new
supplier (opt-in prompt). Stores the supplier GSTIN, the column x-ranges,
header-field anchors, the UOM map, and the default purchase / tax ledger
names for that supplier. The tenant's next bill from the same GSTIN skips
steps 3–5 and parses at high confidence with no LLM.

---

## Tally Purchase voucher XML

Built on **Approve**, offered as a `.xml` download (one file per bill; a
batch download zips them).

```xml
<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY><IMPORTDATA>
  <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
  <REQUESTDATA>
   <!-- master-create messages first, only for NEW masters -->
   <TALLYMESSAGE><LEDGER NAME="Sugal Foods" ACTION="Create">
     <PARENT>Sundry Creditors</PARENT>
     <PARTYGSTIN>19BHBPK1450P1Z3</PARTYGSTIN>
     <GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>
     <LEDSTATENAME>West Bengal</LEDSTATENAME>
   </LEDGER></TALLYMESSAGE>
   <TALLYMESSAGE><STOCKITEM NAME="Monin Mojito Mint Syrup 1000Ml" ACTION="Create">
     <PARENT>Primary</PARENT><BASEUNITS>Pcs</BASEUNITS>
     <GSTDETAILS.LIST><HSNMASTERNAME/><GSTRATE>18</GSTRATE></GSTDETAILS.LIST>
   </STOCKITEM></TALLYMESSAGE>
   <!-- the purchase voucher -->
   <TALLYMESSAGE>
    <VOUCHER VCHTYPE="Purchase" ACTION="Create">
      <DATE>20250825</DATE>
      <REFERENCE>INV2526-5667</REFERENCE>
      <REFERENCEDATE>20250825</REFERENCEDATE>
      <PARTYLEDGERNAME>Sugal Foods</PARTYLEDGERNAME>
      <VOUCHERNUMBER>INV2526-5667</VOUCHERNUMBER>
      <PLACEOFSUPPLY>West Bengal</PLACEOFSUPPLY>
      <UDF:METALERP_REF DESC="METALERP_REF" TYPE="String">ib_9f3c…</UDF:METALERP_REF>
      <ALLINVENTORYENTRIES.LIST> … one per line: stockitem, qty, rate,
          amount, and the line's purchase/CGST/SGST ledger allocations … </ALLINVENTORYENTRIES.LIST>
      <LEDGERENTRIES.LIST> … Purchase A/c (taxable), CGST 9%, SGST 9%,
          Round Off, and the party ledger (credit, grand total) … </LEDGERENTRIES.LIST>
    </VOUCHER>
   </TALLYMESSAGE>
  </REQUESTDATA>
 </IMPORTDATA></BODY>
</ENVELOPE>
```

- **Ledger names** for Purchase A/c, CGST/SGST duty, Round Off come from a
  small per-tenant **`tally_ledger_config`** (set once in the module's
  settings page; sensible GST-standard defaults pre-filled). Supplier
  ledger name = the party's `legal_name`.
- **`UDF:METALERP_REF`** = the `inward_bill` id, so a re-download +
  re-import is detectable, and Tally's own dup-check on `VOUCHERNUMBER`
  is a second guard.
- Dates as `YYYYMMDD`. UTF-16 output (Tally's import default) or UTF-8
  with an explicit `<?xml encoding?>` — confirm against a live import
  early; Tally is picky here.
- **Intra vs inter-state** decided from the two GSTIN prefixes
  (supplier `19`, buyer `19` → CGST+SGST; different → IGST). The sample
  is intra-state.

---

## Schema

New tables (all `tenant_id`-scoped, follow the existing mixin/enum style):

```
inward_bill(
  id, tenant_id, uploaded_by, uploaded_at,
  source_filename, source_pdf_path,          -- PDF on the same volume as invoice PDFs
  supplier_name, supplier_gstin, supplier_pan, supplier_state_code,
  matched_party_id NULL,                     -- set on resolve; null = new supplier staged
  new_supplier_staged_json NULL,             -- party fields we'll create on approve
                                             --   (with source=inward_bill, source_ref=this id)
  bill_no, bill_date, sales_order_ref NULL,
  place_of_supply_state_code, supply_type,   -- INTRA | INTER
  currency DEFAULT 'INR',
  taxable_total, cgst_total, sgst_total, igst_total, cess_total, round_off, grand_total,
  amount_in_words,
  extraction_method ENUM('EINVOICE_QR','TEMPLATE','TABLE','VISION_LLM'),
  extraction_confidence NUMERIC(4,3),
  reconciled BOOLEAN,
  status ENUM('UPLOADED','EXTRACTING','NEEDS_REVIEW','APPROVED','REJECTED','ERROR'),
  tally_xml_path NULL,                       -- generated on approve
  raw_text,                                  -- for re-parse / debugging
  notes,
)

inward_bill_line(
  id, inward_bill_id, sl_no,
  description, hsn, quantity, uom,
  unit_rate, discount_pct, discount_amt, taxable_value,
  cgst_rate, cgst_amt, sgst_rate, sgst_amt, igst_rate, igst_amt, line_total,
  match_method ENUM('EXACT','ALIAS','FUZZY','LLM','NEW','MANUAL') NULL,
  match_confidence NUMERIC(4,3) NULL,
  matched_item_id NULL,
  new_item_staged_json NULL,
  review_flag TEXT NULL,                     -- 'unknown_hsn', 'low_confidence', 'ambiguous', …
)

supplier_template(
  id, tenant_id, supplier_gstin, supplier_name,
  column_ranges_json, header_anchors_json, uom_map_json,
  default_purchase_ledger, default_cgst_ledger, default_sgst_ledger, default_igst_ledger,
  created_from_bill_id, created_at,
  UNIQUE(tenant_id, supplier_gstin)
)

tally_ledger_config(
  tenant_id PRIMARY KEY,
  creditors_group DEFAULT 'Sundry Creditors',
  purchase_ledger DEFAULT 'Purchase Accounts',
  cgst_ledger DEFAULT 'CGST', sgst_ledger DEFAULT 'SGST', igst_ledger DEFAULT 'IGST',
  round_off_ledger DEFAULT 'Round Off',
  xml_encoding DEFAULT 'UTF-16'
)

extraction_run(                              -- audit / retry
  id, inward_bill_id, attempt, method, ok, confidence, error, llm_tokens NULL, at
)
```

Reused as-is: `party` (incl. the `status` / `source` / `source_ref` /
`last_txn_at` columns added in migration `0003` — this module writes
`source = inward_bill` / `source_ref` / `last_txn_at` on approve and does
**not** add columns of its own to `party`), `party_address`, `item`,
`item_alias`, `hsn_code`, `synonym`, `audit_log`, `job`,
`domain/normalize.py`. `ItemSource.auto_from_purchase` and
`PartySource.inward_bill` already exist in `app/models/_mixins.py`.

Flag on the existing `tenant` table: `ext_inward_import BOOLEAN DEFAULT false`.

New tables + `item.last_purchase_rate` / `item.last_purchased_at` +
`tenant.ext_inward_import` land in **migration `0004`** (`0003` is the
party-provenance migration already on `main`).

---

## API (`/api/inward-bills`)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/inward-bills` | multipart PDF upload (one or many). Returns the created `inward_bill` id(s), status `EXTRACTING`. |
| GET | `/api/inward-bills` | list — filters: status, supplier, date range. |
| GET | `/api/inward-bills/{id}` | full record: header, lines with match state, per-field confidence, reconciliation, the resolved/staged supplier. |
| GET | `/api/inward-bills/{id}/pdf` | streams the source PDF for the review view. |
| PATCH | `/api/inward-bills/{id}` | reviewer edits: correct header fields, override a line's `matched_item_id`, change the staged supplier, mark lines. |
| POST | `/api/inward-bills/{id}/re-extract` | re-run extraction (e.g. after a template is saved, or method override). |
| POST | `/api/inward-bills/{id}/approve` | validates reconciliation + every line resolved → creates staged party/items → links lines → builds XML → status `APPROVED`. |
| GET | `/api/inward-bills/{id}/xml` | download the Tally Purchase voucher XML (`APPROVED` only). |
| POST | `/api/inward-bills/{id}/reject` | status `REJECTED`, reason; no masters created. |
| POST | `/api/inward-bills/{id}/save-template` | persist a `supplier_template` from this bill. |
| GET/PUT | `/api/inward-bills/settings/ledgers` | the tenant's `tally_ledger_config`. |

Role gate: `require_write` (owner / accountant) for upload, patch, approve,
reject, template, settings. `viewer` can list + read.

---

## Frontend (`web/src/pages/inward/`)

| Screen | What |
|---|---|
| **`InwardListPage`** (`/inward`) | table: filename, supplier, bill no/date, amount, status badge, confidence. Upload drop-zone at top (multi-file). Row → review. |
| **`InwardReviewPage`** (`/inward/:id`) | split view: left = the PDF (`<embed>` / pdf.js); right = a form. Header block (supplier — matched/new toggle, bill no, date, place of supply); a **totals check** panel (taxable / CGST / SGST / round-off / grand, green when reconciled); a **lines table** — each row shows description, HSN, qty, rate, amount and a **match cell**: `→ SS Utensil (92%)` / `→ NEW item` / a search-combobox to override, plus the `review_flag` chip. Buttons: **Re-extract**, **Reject**, **Approve**. |
| **After approve** | the review page flips read-only with a **Download Tally XML** button and (first-from-this-supplier) a *Save as template* prompt. |
| **`InwardSettingsPage`** (`/inward/settings`) | the `tally_ledger_config` form. Small. |

Nav item "Inward" appears only when `me` → tenant has `ext_inward_import`.

---

## Execution phases

| # | Deliverable | Size |
|---|---|---|
| **X0** | Schema + migration **`0004`** (all tables + `tenant.ext_inward_import` + the two `item` purchase-rate columns; `down_revision = "0003"`), feature-flag plumbing, `/api/inward-bills` skeleton, nav gating, PDF storage on the existing volume. | 0.5 wk |
| **X1** | **Extractor — text path**: pdfplumber table + header/totals regex, e-invoice QR decode, reconciliation gate, `extraction_run` logging. `no-text-layer` detection wired but the image path returns "unsupported" for now. Unit tests against 4–5 real supplier PDFs incl. the Sugal Foods sample. | 1.5 wk |
| **X2** | **Supplier resolution** — GSTIN match → link; no-match → stage new party (name/GSTIN/PAN/state/address, `source=inward_bill` + `source_ref` set at Approve-time creation). PAN-from-GSTIN, state-from-GSTIN. `last_txn_at` bumped on approve. Tests incl. customer→both promotion (original `source` preserved). | 0.5 wk |
| **X3** | **Line resolution** — the 5-step ladder: exact / alias / trigram+HSN / LLM disambiguation (batched, tenant-catalogue-only) / new-item stage. Confidence + `review_flag`. Introduces the shared `llm` service module. Tests: exact, fuzzy win, HSN tie-break, ambiguous→LLM, new. | 1 wk |
| **X4** | **Review UI** — `InwardListPage` + `InwardReviewPage` (PDF pane, header form, totals check, lines table with match override), `PATCH` wiring. | 1.5 wk |
| **X5** | **Approve + XML** — stage→create party/items in one txn (supplier gets `source=inward_bill` / `source_ref`; matched/created party `last_txn_at` bumped forward-only), link lines, `tally_ledger_config`, Purchase `VOUCHER` builder (+ `<LEDGER>`/`<STOCKITEM>` for new masters, `UDF:METALERP_REF`, intra/inter split), `/xml` download. **Validate against a real Tally import.** | 1 wk |
| **X6** | **`supplier_template`** — save-from-bill, apply-on-next, the "save as template?" prompt, re-extract with a template. Settings page. | 0.5 wk |
| **X7** | **Extractor — image path**: `pymupdf` page render + the vision-LLM call through the X3 `llm` module (reuses its client + token logging), `extraction_method = VISION_LLM`, page images kept for the review pane. Batch upload → `job`-queued extraction. Hardening, docs. | 0.5 wk |
| **Total** | | **~7 weeks**, fully independent of the M1 billing critical path (can run in parallel with a second dev, or after M1). |

---

## Decisions to confirm

1. **LLM in X3 from the start, or ship X3 fuzzy-only and add the LLM
   disambiguation call once real bills show the fuzzy miss rate?**
   (Recommend: fuzzy-only first — the shared `llm` module still lands in
   X3 as a stub so X7's image path can build on it without rework.)
2. **`tally_ledger_config` defaults** — are `Purchase Accounts` / `CGST`
   / `SGST` / `Round Off` the right default ledger names, or does the
   shop's chart of accounts use different ones? (One-time per tenant,
   but the defaults should match the common case.)
3. **XML encoding** — confirm UTF-16 vs UTF-8 against the target Tally
   version on the first real import; bake the winner into the default.
4. **Batch upload volume** — how many inward PDFs per day per tenant?
   Sets whether X7's job-queue is needed or a nice-to-have.
5. **Vision-LLM cost ceiling** — image extraction is one multi-page
   vision call per scanned bill. If a tenant dumps hundreds of scans,
   that adds up. Worth a per-tenant monthly page-render cap (soft warn,
   hard stop) — decide the numbers before X7.

---

## Future (out of scope here)

- **Live push** — a per-tenant connector agent (Python, Windows service,
  dials out) that takes `APPROVED` bills off a `job` queue and POSTs the
  same XML into a running Tally on the shop LAN (`:9000`), reports the
  voucher GUID or `<LINEERROR>` back. Idempotent via `UDF:METALERP_REF`.
  Same XML this module already produces — the agent is the only new
  build. Belongs with the Stage-4 purchase/inventory work.
- **Purchase → stock** — once inventory (Stage 3) exists, an approved
  inward bill increments `item.stock_qty`.
- **3-way match** — inward bill against a purchase order + goods receipt.

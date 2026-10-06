# Supplier documents: merging Inward bills and Supplier catalogs

Status: **BUILT 2026-10-06** (Phase 1 front door at `/documents`; Phase 2 supplier-code and remembered-wording bill matching; Phase 3 price history). Not built, by decision: moving inward PDFs to shared storage, one shared supplier resolver, part deliveries. Details and what came after: `PLAN-erp-foundation-masters-documents-media.md` (build notes and Closeout).

## 1. Goal

Today a supplier PDF goes in through one of two doors: **Inward** (a bill: money, GST, a Tally
purchase voucher) or **Catalogs** (a price list: photos, margin, labels, customer catalog). Both
read a supplier PDF into rows, match the rows to items, and let the user review them. They
should look and behave like one thing, share their plumbing, and share one idea of "the same
product", while keeping the parts that really differ.

Out of scope, on purpose: **stock tracking and reconciliation.** Tally stays the source of truth
for stock. This plan only leaves cheap hooks so it can be added later (section 7).

## 2. Decisions

Locked (from you):
- Stock reconciliation is not in scope but must be expandable.
- Tally remains the source of truth for stock; we never write stock quantities ourselves.
- The two document types stay separate underneath; the merge is the front door and the plumbing.

Defaults used in this plan (change cheaply, say so):
- Name: **Supplier documents** (nav label, routes `/documents`).
- The type is **auto-detected with an override**, never silently final.
- The supplier price history (Phase 3) is **in the plan, built last**, and can be parked.

## 3. What is the same and what is not

| | Inward bill | Price list |
|---|---|---|
| Input | PDF | PDF |
| Supplier | read from the PDF, matched to a party | picked at upload |
| Rows | description, qty, rate, GST | name, pack, cost, photo |
| Item matching | `services/inward/resolve_lines.py` (exact, alias, fuzzy), stages new `Item` | `services/catalog/products.py` registry by (supplier, supplier code), same-name suggestions |
| Storage | bind-mounted volume path | `CatalogStorage` (R2 or local) |
| Effect | reconcile totals, approve, Tally purchase voucher, `last_purchase_rate` | margin, labels, customer catalog, promote, Tally stock items |

**Stays separate:** the bill's totals, GST, reconcile and approve (`inward_bill`,
`inward_bill_line`); the price list's margin, outputs and Tally stock-item push
(`supplier_catalog*`, `catalog_product`). No shared table for the two.

**Becomes shared:** the front door, supplier resolution, storage, the extractor interface, and
above all **item identity and matching** (section 5).

## 4. Phase 1: one front door (UI and routing, no data change). About 2 to 3 days

- **Nav:** one entry, "Supplier documents", replacing Inward and Catalogs. Shown when either
  `ext_inward_import` or `ext_supplier_catalog` is on. Old routes (`/inward/*`, `/catalogs/*`)
  redirect.
- **One upload box.** Type detection (a bill has a GSTIN, a bill number and totals; a price list
  is a photo grid with price lines and no totals) picks the flow; the user sees "Looks like a
  bill / price list" with a switch. A flag that is off removes that choice. The supplier is
  required for a price list and read from the PDF for a bill, with a picker when it cannot tell.
- **One list.** Bills and price lists together, filter chips "Bills | Price lists", same columns:
  supplier, date, items, status. Existing review screens open from it unchanged.
- **Supplier page:** the Catalogs tab becomes **Documents**: both kinds for that supplier, product
  count, and a spend total from bills.
- Keep: inward settings (ledgers) under Tally settings; the debug page stays operator-only.
- Tests: route redirects, detection on the sample bill and the two sample catalogs, flag-off
  behaviour, supplier tab.

## 5. Phase 2: shared plumbing and one item identity. About 4 to 5 days

### 5.1 One canonical item
`Item` is the anchor. A supplier's offer (price-list row) and a bill line are both **supplier
lines**, mapped to an item by one mapping:

`(tenant, supplier, supplier code or normalised name) -> item`

`catalog_product` already is this mapping plus the code; it gains a stable `item_id` earlier
(promote can become lazy: created when a row is first used, not only on "Add to items"). Bill
lines record the same link. A confirmed match is **remembered** (the item alias table already
does this for bills).

### 5.2 One matching ladder (both document types)
1. supplier code under this supplier (exact);
2. remembered alias;
3. normalised name, then synonyms (`BARTAN_SYNONYMS`, keep small);
4. fuzzy within the same group (`word_similarity` floors as today, **do not rewrite to `%>`**);
5. otherwise ask. Cross-supplier hits show "Same as your item X (from Supplier A)?" in the review
   grid, with confirm / not the same, exactly like today's suggestions but fuzzy and shared with
   bills.

### 5.3 Groups across suppliers
A supplier's group name maps once to one of your groups and is remembered (extends
`item_category` and the existing Tally `stock_group_map`; no new concept).

### 5.4 Plumbing
- **Storage:** move inward PDFs to `CatalogStorage`. Migration copies existing files; old paths
  keep working until copied. Infra check first (API build context is `api/` only).
- **Supplier resolution:** one resolver (GSTIN, phone, exact name, fuzzy; `resolve_party`, do not
  reorder), used by both flows.
- **Extractor interface:** `extract(pdf) -> header + lines`. The deterministic grid parser and the
  bill text/LLM path plug in behind it. One background-job runner for long extractions.
- Tests: ladder order, cross-supplier suggestion, remembered match, bill line and price-list row
  resolving to the same item, storage migration, no regression in the 500-test suite.

## 6. Phase 3: supplier price history (parkable). About 3 days

- Per product and supplier: what they **quote** (price list, dated) and what you **paid** (bill
  line, dated). Both feed `item.last_purchase_rate`.
- New price list shows "last bought at X, now quoted Y" per row, and a price-change filter.
  This is the catalog's planned re-import price diff (S6), done once for both.
- Table: `supplier_price_point (tenant, product/item, supplier, source: quote|bill, source_id,
  price, pack, on_date)`. Written when a price list is imported or a bill is approved.

## 7. Hooks left for stock (not built)

Stock stays out of scope and Tally stays the truth. So that it can be added without rework:
1. Every bill line and price-list row links to one canonical item (5.1). A future stock view
   needs exactly this link.
2. `item.is_stock` (bool, default true for goods) added now and read by nothing. Transport,
   packing and service lines on a bill will set it false.
3. Approved bill quantities and units stay as stored today, so movements can be derived later.
4. If stock is ever shown, it is a **read-only comparison with Tally's closing stock** (needs the
   stock-summary pull the importer does not support yet, see `tally-stksum-vs-masters`). We never
   write stock quantities, so there is no second ledger to drift.

## 8. Risks

| Risk | Handling |
|---|---|
| Detection guesses wrong | Always shown, one click to switch; never auto-approves anything |
| Moving inward storage breaks old bills | Copy first, keep old paths readable, migrate in a batch with a count check |
| Fuzzy cross-supplier match merges two different products | Never automatic: always a confirm; ambiguous stays a suggestion |
| Two flags, one nav | Nav shows when either is on; each flow still gated by its own flag |
| Scope creep into stock | Section 7 is the only stock work; anything more is a new plan |

## 9. Order and estimate

| Phase | Days | Depends on |
|---|---|---|
| 1 Front door | 2 to 3 | nothing |
| 2 Shared plumbing and item identity | 4 to 5 | 1 |
| 3 Price history | 3 | 2 |

Do Phase 1 first: it removes the duplication you can see at once, with no risk to either flow.

## 10. Open questions (defaults above apply until you answer)

1. Name: "Supplier documents" or "Purchases"?
2. Detection: auto with override (default) or always ask?
3. Phase 3: keep in this plan (default) or park it?
4. Should "Add to items" become automatic the first time a row is used, or stay an explicit step?

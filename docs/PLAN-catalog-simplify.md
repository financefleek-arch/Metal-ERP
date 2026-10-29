# PLAN: Simplify the catalog. Price list in, PDF out; items are optional

Status: BUILT 2026-10-29, uncommitted (S1, S2, S3 and a first S4; migration head `0065`). See section 9 for what shipped and what is left. Supersedes the "Make customer catalog comes after Add to
items, once in stock" flow in `docs/PLAN-erp-foundation-masters-documents-media.md` (decisions A3, A27,
gaps G2, G10) and step 3 of `docs/PLAN-catalog-review-declutter.md` (4.2, 4.6). The rest of the declutter
plan (filters, bulk bar, rows) still stands.

## 1. The principle (user, 2026-10-29)

- A **price list is marketing material, not an indicator of stock.** Importing one says nothing about what
  the shop holds.
- A **bill** is what may indicate stock (goods arrived). Tally stays the truth for quantities (A11, A13).
- So the product is: **PDF in, PDF out**, and the shop chooses whether the products also become Items.
  Nothing about the PDF may depend on an availability flag.

## 2. What the code does today (verified in code, 2026-10-29)

| # | Fact | Where |
|---|---|---|
| 1 | Add to items creates every new item **Out of stock** ("price-list items are not stock until you mark them") | `services/catalog/promote.py:175` |
| 2 | The customer catalog prints **only In stock** items (Expected on request); Out of stock / Discontinued are "left out" and counted | `services/catalog/item_catalogs.py:160-182` (`plan`) |
| 3 | Result: price list -> Add to items -> **Set availability to In stock** -> Make customer catalog. Skip the middle step and the PDF comes out empty or short. | 1 + 2 |
| 4 | The same `plan()` feeds the **public share link** and **order placement**, so an Out of stock item is invisible on `/c/<token>` and an order for it is refused with "no longer available" | `services/share_links.py:public_view`, `services/orders.py:183` |
| 5 | A workaround setting exists: supplier default **"Mark them In stock"** (+ "Add automatically"), and `mark_in_stock` on promote | `services/catalog/supplier_defaults.py:80`, `routers/catalog_tally.py:92`, `models/catalog.py:178`, `SupplierDefaultsCard.tsx`, `schemas_catalog.py` x3 |
| 6 | Availability controls on the output: "Include expected", "Label as On order", "left out: N out of stock, N expected, N discontinued", share link `include_expected` | `MakeCatalogDialog.tsx:273,432-456`, `ShareLinkDialog.tsx`, `schemas_customer_catalog.py`, `CatalogShareLink.include_expected` |
| 7 | A bill sets the item In stock on approve (never Discontinued). This is the one writer that matches the principle. It also stamps `last_purchased_at`. | `services/inward/approve.py:235` |
| 8 | There is **no PDF out of a supplier price list**. The only output built straight from a supplier catalog is barcode labels. A customer PDF can only be made from Items. | `routers/catalog.py:1172` (labels), `item_catalogs.py` (docstring: "never straight from a supplier price list") |
| 9 | Order review shows a "not in stock now" tag per line from the same flag | `routers/orders.py:270`, `OrderDetail.tsx:184` |
| 10 | Items page also has availability filter, Excel column, and a bulk "Set availability" action | `item_filter.py:29`, `item_sheet.py`, `BulkPanel.tsx` |

The clutter is items 1-6: one flag, written by the wrong event (a price list), read by three outputs (PDF,
link, orders), and patched by one supplier setting and three catalog-dialog options.

## 3. Target design

```
Supplier PDF  ->  review (read, margin, group, photos)  ->  three independent exits
                                                             A. Customer PDF   (download, no items needed)
                                                             B. Add to my items (optional, for billing / Tally / labels / links / orders)
                                                             C. Both
```

- **A. PDF out directly from the price list.** New "Customer PDF" action on the price list page: our
  selling price, our code, our groups, the extracted photo, pack size. Same renderer (`render_pdf`) and the
  same layout options (columns, group by, code, price basis, contents) as today. No cost price, no supplier
  code (same guard as `CatalogEntry`). Respects the row's Included/Excluded flag, which is the only
  inclusion control.
- **B. Add to items stays exactly as it is, minus the stock side effect.** Price-list items are just
  items. They are neither in nor out of stock.
- **Items -> Make customer catalog stays** for shops that maintain items (selection by filter, versions,
  rebuild, out of date). It just stops filtering on availability.
- **Public link and orders require items** (an order line is an item that becomes an invoice line). So
  "Share as a link" is only offered after B; A needs nothing.
- **Availability stops gating anything.** Inclusion in a catalog, link or order = the user's selection and
  "has a price" (and not archived / merged). Nothing else.

## 4. Decisions needed (defaults chosen, change cheaply)

| # | Question | Default |
|---|---|---|
| D1 | Do In stock / Expected / Out of stock gate catalog, link or orders at all? | **DECIDED 2026-10-29: No.** Nothing is hidden because of availability. The selection (ids or an Items filter) is the control. |
| D2 | Hide Discontinued automatically? | **DECIDED 2026-10-29: No.** "We don't hide anything that was in the PDF." Only archived / merged items and items with no price are skipped. |
| D3 | Is a PDF from a price list rendered on every download? | **DECIDED 2026-10-29: No, cached by content.** Fingerprint = included rows (name, code, group, price, pack, photo hash) + layout + title + cover. Same fingerprint = return the stored PDF; any edit changes it and the next download re-renders. A cache, not versions: no list, old copies pruned. Large lists use the existing `catalog_output_job` pattern. Stored named versions only if price-list-on-request is built. |
| D4 | What happens to the `item.availability` column and Items filter / Excel column / bulk "Set availability"? | **Keep as a plain hand-set label.** Not read by any output. Revisit when stock work (G34) starts. No migration now. |
| D5 | Should a bill still set In stock? | **Keep** (harmless, and true). Optional later: an Items filter "Bought in last N days" on `last_purchased_at`, which is the real bill-based signal and already stored. Not now. |
| D6 | Existing items that were deliberately set Out of stock will now appear in catalogs. | **Accepted** (follows from D1/D2). Release note only. |
| D7 | Keep the "On order" label? | **DECIDED 2026-10-29: let it be.** Items hand-marked Expected keep the tag; it hides nothing and has no include toggle. |
| D8 | From Items, an option to leave out Out of stock items? | **Yes, one opt-in checkbox "Leave out items marked Out of stock", default OFF.** Stored in the layout (rebuild repeats it) and as a share-link option (live). Dialog shows "N left out" when on. Replaces include_expected / label_expected / always-on filter. Not offered for price-list PDFs. Discontinued is never auto-hidden (D2). |

## 5. Work, in slices (each shippable; backend first, tests on SQLite with FKs on)

**S1. Stop gating on availability (small, high value).**
- `item_catalogs.plan`: replace the `allowed` set with the opt-in `leave_out_of_stock` flag (D8) and keep a single `left_out` count for it; skip archived / merged
  and no price (nothing else). `Plan.left_out` goes away; `CatalogCheckOut.left_out_*` collapse to one `left_out` count.
- `share_links.public_view` and `orders.py` placement use the same `plan()`, so link and orders are fixed
  by the same change. Keep the `ON_ORDER` tag for items hand-marked Expected (D7); it no longer affects inclusion.
- Add `leave_out_of_stock: bool = False` to `CatalogLayout` and `CatalogShareLink` (migration: one boolean column). Remove `include_expected` / `label_expected` from `CatalogLayout`, `CatalogShareLink` use (leave the DB
  column, stop reading it; drop in a later cleanup migration), `MakeCatalogDialog`, `ShareLinkDialog`,
  `ShareLinkOptions`, `lib/catalog.ts`, and the "left out" line.
- `is_stale` keeps working (it calls `plan`).
- Order review: remove `available` from `OrderLineOut` and the "not in stock now" tag.
- Tests: update `test_item_catalogs_api.py`, `test_customer_orders.py`, `test_items_hub.py` (availability
  cases become "everything with a price is included"); add one test that a fresh price-list item appears in
  a customer catalog, a link and an order with no availability change.

**S2. Remove the workaround.**
- `promote()` no longer sets `availability=out_of_stock` (use the column default). Remove `mark_in_stock`
  from `promote` body, `supplier_defaults.add_automatically`, `SupplierCatalogDefault` use (leave column,
  stop reading; drop later), `SupplierDefaultsCard` toggle, `schemas_catalog.py` (3 places),
  `routers/catalog_review.py`, and the "Update item prices" / undo copy that mentions stock.
- Keep "Add automatically" (that is a real choice: put products in my items on import, or not).
- Docs and the Items "Came from" text: delete wording like "starts Out of stock".

**S3. PDF out of a price list.**
- `services/catalog/catalog_pdf` is reused as is. New `services/catalog/supplier_pdf.py`: rows
  (`SupplierCatalogItem`, included only) -> `CatalogEntry` (`code` = our code, `name` = display name,
  `group` = category name, `price` = `sell_price`, `pack_qty`, `image` = stored crop re-encoded to JPEG
  from `image_key`). Order = catalog order (page, position) or grouped, per the existing `group_by`.
- Cache (D3): `fingerprint()` over the inputs above; stored at `supplier-catalogs/{tenant}/{catalog}/pdf/{fingerprint}.pdf`; `prune` keeps the latest few per catalog and deletes them with the catalog.
- `POST /api/supplier-catalogs/{id}/customer-pdf` (+ `/check`, + job poll/file) mirroring the labels
  endpoints: 200 PDF up to 100 rows, 202 job above. Body = `CatalogLayout` + `title` + optional
  `ids` / `filter` / `all_included` (same target rule the bulk endpoints use). Cover from `brand_for`.
- Missing photo = placeholder (same as today). Rows with a zero price are skipped and counted.
- Tests: no cost / supplier code in the PDF text, included-only, group order, large -> job, tenant isolation.

**S4. One review page, three exits (UI; mock first per ui-plan-guardrails).**
- Replace the declutter plan's step 3 ("Add to items", then "Make customer catalog") with an **Outputs**
  row, always available once prices are set:
  `[ Download customer PDF ]  [ Add to my items ]  [ Print labels ]`. After items exist the row adds
  `[ Share as link ]` and `[ Open in Items ]`.
- `MakeCatalogDialog` is reused for the PDF layout choices (columns, group by, price basis); it gets a
  `source = price list | items` prop instead of a second dialog.
- Remove the hidden-until-items "Make customer catalog (N)" header button (declutter plan 4.1 already
  wants it gone) and the post-add "make a catalog" nudge becomes just one of the exits.
- The Documents upload "done" screen links straight to this row.

**S5. Cleanup (later, separate migration).** S1 adds one column (`leave_out_of_stock`). Drop `catalog_share_link.include_expected` and
`supplier_catalog_default.mark_in_stock`.

Order: S1 -> S2 -> S3 -> S4 (S1 and S2 are pure removals and fix the real bug today; S3 is the new
capability; S4 is the screen).

## 6. Click budget

| Goal | Today | After |
|---|---|---|
| Customer PDF from a price list | Add to items -> Set availability In stock (bulk) -> Items -> select -> Make customer catalog -> Make = 5+ steps, 2 pages | Open the list -> Download customer PDF = **2** |
| Same, and also keep the products as items | as above | Add to my items -> Download PDF = 2 |
| Customer link with ordering | Add -> set In stock -> select -> Share link | Add -> Share as link = 2 |
| Repeat supplier, nothing changes except prices | needs "Mark them In stock" configured | Upload -> Download PDF (or auto-add) = 2 |

## 7. Risks and traps

- **R1 (D6).** Existing deliberately-Out-of-stock or Discontinued items become visible in catalogs. Accepted; add a release note.
- **R2.** `item_catalogs.plan` is shared by PDF, link, order placement and `is_stale`. Change it once, in
  one commit, and run the whole catalog + orders suite (not only `test_item_catalogs_api.py`).
- **R3.** Public surface stays an allow-list: the new PDF path must never carry `cost_price` or
  `supplier_code` (`CatalogEntry` has no such fields; keep it that way; add the no-leak test).
- **R4.** Supplier rows' images live under `image_key` (private storage), not `media_asset`. Convert to
  JPEG in memory for the PDF; do not create media assets for a download.
- **R5.** Parity: this removes controls, adds one action. No new routes in Fleek's control plane, no new
  env vars, no new secrets.
- **R6.** Tests run on SQLite with FKs ON, prod is Postgres (traps in `catalog-arc-closeout-2026-10`). S1-S3
  add no FKs and no migrations; S5 is the only migration.
- **R7.** `DocumentsPage.tsx` uncommitted default-export problem noted in the declutter plan (section 8)
  must be settled before S4 touches that page.
- User does the commits; nothing here is committed.

## 8. Out of scope

Stock quantities (G34), supplier purchase orders, named/versioned price-list PDFs (D3), cover
customisation (parked), a bill-based "recently bought" filter (D5), WhatsApp sending of the PDF (paid
Marketing message; see `whatsapp-promotional-cost-rule`).

## 9. Build notes (2026-10-29)

Built, in this order. All uncommitted; the user does the check-in.

- **S1/S2 (API).** `item_catalogs.plan` no longer reads availability: it skips archived / merged items and
  items with no price, and (only when `leave_out_of_stock`) Out of stock ones, counted in `Plan.left_out`.
  The public link, order placement, offers and `is_stale` all go through it, so they follow. Items marked
  Expected keep an "On order" tag (always, no option). `CatalogLayout` and `CatalogShareLink` gained
  `leave_out_of_stock` (migration `0065`); `include_expected`, `label_expected`, `left_out_*`,
  `mark_in_stock` (promote body, supplier defaults, `promote.py` Out of stock default) and the order line
  `available` flag are gone. The old DB columns `catalog_share_link.include_expected` and
  `supplier_catalog_default.mark_in_stock` remain, unread (S5 drops them).
- **S3 (API).** `POST /api/supplier-catalogs/{id}/customer-pdf` (+ `/check`): `services/catalog/supplier_pdf.py`
  and `routers/catalog_customer_pdf.py`. Result is a `catalog_output_job` (kind `price_list_pdf`); download
  with the existing `/outputs/{job}/file`. Content-hash cache: an identical request returns the earlier
  done job; up to 100 rows build in the request, more in the background. 7 tests in
  `tests/catalog/test_price_list_pdf.py` (no items created, no cost/supplier code, cache hit and
  invalidation, selection, background, tenant isolation).
- **S4 (web), first version.** `PriceListPdfDialog` and a "Customer PDF" button in the price-list page
  header (uses the selected rows when some are ticked, else all included). `MakeCatalogDialog` and
  `ShareLinkDialog` now carry the single "Leave out items marked Out of stock" checkbox; the "Mark them In
  stock" toggle and the order "not in stock now" tag are removed. typecheck and eslint clean.
- **Not done / next.** The Outputs row on the review page (declutter plan step 3) and Share-as-link
  after Add to items are not re-laid-out; the button sits in the header until the visual review. The
  `leave_out_of_stock` option is not editable on an existing link (only at creation). S5 cleanup migration.
  Not browser-checked.

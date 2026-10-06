# Plan: item and supplier foundation, documents, and media (ERP-aligned)

Status: **BUILT (H1-H6 and MP), committed and deployed as of 2026-10-06** (migration head 0048). Written 2026-10-05; the sections below keep the original plan, the register and the per-slice build notes show what was done. See "Closeout" at the end for what is left, what was dropped or parked, and what comes next.
Owner: Fleek. Supersedes nothing; it is the umbrella over
`EXECUTION-PLAN-supplier-catalog.md` (built, section 17), `EXECUTION-PLAN-supplier-documents.md`
(detail for workstreams B, C and F) and the Tally roadmap in `MASTER-tally-complement-saas.md`.

How to read this: section 2 is the **decision register** (everything agreed, everything still open
with the default we will use). Section 3 is the **architecture principles** every piece must follow
so the product can grow into a full ERP. Sections 4 and 5 are the **workstreams and the execution
breakdown**. Section 8 lists what blocks on which open decision.

---

## 1. Where we are (built, uncommitted unless stated)

- **Supplier catalog module** (flag `ext_supplier_catalog`), migrations 0032 to 0038: PDF import,
  review and edit, groups, margin pricing, barcode labels, customer catalog PDF, product registry
  with group codes, promote to items, batched Tally stock-item push. 500+ tests, live-verified
  against the dev TallyPrime, browser-verified.
- **Inward bills** (flag `ext_inward_import`): bill PDF to purchase voucher. Live-verified earlier.
- **Parties**: master with CRUD, dedupe, role (customer / supplier / both), archive. New: a
  Catalogs tab per supplier, delete and demote guards, errors surfaced.
- **Tally connector** (F1): masters in, sales and purchase vouchers out, stock items out.
- Just built (this week, UI): "Add to items" dialog, photo zoom, supplier picker with
  "upload without a supplier", supplier required by default.
- **Known gaps found but not yet fixed:** the push progress stalls for minutes (section 4, E1);
  promote does not copy the catalog photo to the item; `Item` has no photo; the .NET agent with the
  `push_items` case is built locally but not released.

---

## 2. Decision register

### 2.1 Agreed (do not re-litigate; change only by saying so)

| # | Decision | Source |
|---|---|---|
| A1 | Supplier catalog is its own module behind `ext_supplier_catalog`; owner and accountant write, viewer reads | catalog plan |
| A2 | Pricing is **margin %**: one bulk margin per catalog plus a per-item override; call it "margin" everywhere; filters "bulk margin" and "item level margin" | you |
| A3 | The supplier's "in stock" flag is ignored | you |
| A4 | Group basis is **product type, one level**, auto-created as `item_category`, always user-editable (rename, merge, delete); brand is an attribute only | you |
| A5 | New groups are created automatically by default, with an opt-out policy | you |
| A6 | ~~Group-based codes, provisional then locked~~ **Superseded by A25.** The product registry keyed by (supplier, supplier code) stays | you, approved |
| A25 | **Item codes are one firm-wide running number, six digits from 100001, immutable from creation** (decided 2026-10-05). No group, supplier or meaning in the code; the group is a separate field and regrouping never touches a code. Optional firm prefix in front of new codes. No provisional/locked state. Existing data was overwritten (migration 0039), not preserved: not in production | you |
| A7 | The supplier is chosen at upload; a supplier is a **party** (no separate supplier master); suppliers get full CRUD through Parties | you |
| A8 | The customer catalog cover is fixed and automatic; cover customisation is backlog | you |
| A9 | Customer catalog options are chosen at creation (columns, grouping, show code, price per pack or piece, contents page) | you |
| A10 | "Add to items" (promote) is an explicit step, independent of Tally | you |
| A11 | **Tally is optional**, and is the source of truth for books and stock | you |
| A12 | Tally push: selected items only, opt-in; part number only (never an alias); names unique; we only create what a fresh check shows missing; a name already in Tally is a blocker; batches, stop on first rejected batch | built and verified |
| A13 | **Stock tracking and reconciliation are out of scope** but the design must be expandable; we never write stock quantities | you |
| A14 | Bills and price lists stay separate document types underneath; the merge is the front door and the shared plumbing | you |
| A15 | One item-matching ladder across suppliers and document types (code, remembered alias, name, fuzzy in group, ask); matches are confirmed by a person and remembered | you |
| A16 | Items can carry a photo; the **catalog goes with a photo**; catalog generation can embed a photo for items that lack one | you |
| A17 | Photos stay on our side; Tally gets no images | design |
| A18 | Never commit or push for the user; the user does check-ins | standing |
| A20 | **Selling price goes to Tally** (decided 2026-10-05): sent as the item's standard selling price; re-sent when our price changes; probe the Tally XML on the dev company before building (E4) | you |
| A21 | **Customer catalogs from the item list are wanted, staged** (decided 2026-10-05): photo upgrade first, item-list source as M2b | you |
| A22 | Review grids offer a **Possible matches filter** with a count, and each suggestion shows the matched product's name, code and supplier (built 2026-10-05) | you |
| A23 | **Supplier-first upload flow** (decided 2026-10-05): add the supplier as a party (or find it), pick it, then choose the PDF from that supplier, then say what it is: an **inward bill** or a **catalog**. The supplier is always the user's pick; a name read from the PDF is only a cross-check. Also reachable from the supplier's own page ("Upload from this supplier") | you |
| A24 | **A catalog can be created without a supplier** (decided 2026-10-05): the supplier is good to have, never required. A bill always needs one (a purchase voucher needs a party ledger). A supplier can be attached to a supplier-less catalog later (built). Without a supplier: no match on re-upload, no per-supplier price history; same-name suggestions across files still work | you |
| A26 | **Items are the hub; customer catalogs, labels, Tally push, photos and bulk edits start from Items; an item must exist before a customer catalog can include it** (decided 2026-10-06). Supplier documents (price lists and bills) only bring items in. Layout reviewed in the Items Hub Layout artifact | you |
| A27 | **Item availability** (decided 2026-10-06): In stock, Expected, Out of stock, Discontinued. A status, not a quantity; Tally stays the truth for stock figures. Items from a **supplier price list start Out of stock** (bulk "Set availability" to In stock, one supplier default "mark in stock when adding", off unless chosen); bills and hand-added items start In stock; approving a bill sets its items In stock. Customer catalogs include **In stock only by default**; Expected can be added, and the "on order" label on Expected items is optional, never default. Not the same as the unused `is_stock` goods-vs-service flag | you |
| A28 | **Bulk is the guardrail** (decided 2026-10-06): every action takes a selection by filter, shows a count first, runs in the background when large, and can be undone or reports failures; one-click "Add all included to items"; supplier defaults (margin, rounding, group map, add automatically, mark in stock) so repeat imports need no decisions; review by exception; Excel round trip; photo queue | you |
| A19 | Visual review before UI coding; mobile-first; money is Decimal; Part 1 of the party and item guardrails in memory apply | standing |

### 2.2 Open, with the default we will use until you answer

| # | Question | Default | Blocks |
|---|---|---|---|
| O1 | Nav and route name for the merged area | "Supplier documents", `/documents` | M3 |
| O2 | Document type after the PDF is chosen: auto-detect with override, or always ask | auto-detect as a pre-selected choice the user confirms (never silent) | M3 |
| O3 | Does "Add to items" become automatic the first time a row is used | stay explicit | M4 |
| O10 | ~~Supplier name read from the PDF~~ **Settled by A23:** the supplier is the user's pick. The sample catalogs print no supplier name anyway. A name or GSTIN found in a bill is used only to warn on a mismatch | none |
| O6 | When a catalog photo arrives via promote, copy automatically or after a confirm | **copy automatically; an item's own photo is never replaced** | M1 |
| O7 | Missing photo at catalog build time: block, or placeholder | **placeholder with a warning** | M2 |
| O8 | Supplier price history (quote versus paid) inside this plan | yes, built last, parkable | M5 |
| O9 | Photo model: one primary photo or a gallery | **media table now, one primary photo in the UI**, gallery later | M1 |

### 2.3 Parked on purpose

Stock ledger and reconciliation; catalog cover customisation; vision and speech capture; GST
billing; reminder scheduler; purchase orders and goods receipt; multi-location. Each has a hook in
section 3 so it can be added without reworking what is built here.

---

## 3. Architecture principles (what keeps this ERP-shaped)

1. **Masters are canonical, documents point at them.** `Party` and `Item` (and `item_category`) are
   the only masters. Bills, price lists, invoices and catalogs reference them; they never keep a
   private copy of "the product". The product registry (`catalog_product`) is the
   *supplier-to-item mapping*, not a second item.
2. **One pipeline per concern.** One upload and storage path, one supplier resolver, one item
   matcher, one media service, one background-job runner, one Tally outbox. Document types plug in
   behind these. No new feature gets its own copy.
3. **Document types are plugins.** A type declares: how to extract rows, what a row means, what
   approving it does. Bill = money and a voucher. Price list = offers, margin and outputs. A future
   goods receipt, purchase order or quotation is another type, not a new stack.
4. **Tally is the book of record.** We read from it and push to it; we never keep a competing
   ledger or stock figure. Anything stock-related is a read-only comparison.
5. **Media is a platform service.** One `media_asset` table and storage layer, owned by any entity
   (item now; party logo, documents, attachments later). Content-addressed, thumbnailed, signed
   URLs, tenant-scoped.
6. **Every automatic match is a suggestion a person confirms, then remembered.** Never silent merges
   of items or parties.
7. **Feature flags per module, shared plumbing ungated.** Nav appears if any module in an area is
   on; each flow stays gated by its own flag.
8. **Long work is a job with honest progress.** Anything over a few seconds runs as a job and
   reports its phase, count and an estimate, including when it is waiting on an external agent.
9. **Everything auditable and tenant-scoped.** New entities get tenant ids, audit entries on
   consequential actions (promote, push, approve), and role checks (owner and accountant write).
10. **Additive migrations, back-filled, tested.** No destructive change without a copy-then-switch.
11. **Expandable hooks, not speculative features** (section 7).

---

## 4. Workstreams

### A. Item master and media
- A1. `media_asset` table (tenant, owner type and id, kind, storage key, thumbnail key, sha256,
  width, height, bytes, source, created by). Unique on (tenant, sha256, owner) to avoid duplicates.
- A2. `item.primary_media_id`; `item.is_stock` (bool, default true, unused for now, section 7).
- A3. Upload endpoint with resize to about 1200 px and a thumbnail; accepts jpg, png, webp, and
  phone camera images; size and type limits; EXIF rotation applied.
- A4. Item list and item page: thumbnail, add, replace, remove photo; mobile camera capture.
- A5. Bulk attach: drop many files, matched to items by code or name, with a review step.
- A6. **Promote copies the catalog photo** to the new item (never overwrites an existing one).

### B. Supplier documents front door (see the supplier-documents plan, Phase 1)
- B1. One nav entry and one list with filters. **One upload flow, supplier first (A23):**
  1. supplier picker (search, or add a new supplier inline as a party with role supplier; archived
     and customer-only parties handled as today);
  2. choose the PDF (drag or pick); the supplier stays shown;
  3. "What is this?" Inward bill / Catalog, pre-selected by detection, one click to change;
  4. the matching flow opens (bill review, or catalog review) with the supplier already set.
  The bill flow stops resolving the supplier from the PDF: it uses the picked one and only
  **cross-checks** the PDF (GSTIN or name differs: a warning, never a silent switch or an
  auto-created supplier). Supplier-less upload stays available for catalogs only, behind the
  existing opt-out, which becomes a first-class "No supplier" choice next to the picker (A24),
  not a hidden checkbox.
- B2. Supplier page "Documents" tab: bills and price lists, product count, spend from bills.
- B3. Old routes redirect; flags decide what is offered.

### C. Matching and identity (Phase 2)
- C1. One resolver for items: supplier code, remembered alias, normalised name and synonyms, fuzzy
  within group, else ask. Used by bills and price lists.
- C2. Cross-supplier "same as your item X?" in the review grid, confirm or reject, remembered.
- C3. Group mapping across suppliers, remembered, shared with the Tally group map.
- C4. One supplier resolver (GSTIN, phone, exact, fuzzy; do not reorder) for both flows.
- C5. Inward storage moved onto the shared storage layer, copy first.
- C6. One extractor interface (`extract(pdf) -> header and lines`), grid parser and bill parser
  behind it; one job runner.

### D. Catalog output
- D1. Customer catalog dialog counts included items with no photo, links to a filtered view, and
  offers Add photo per item (file or camera) and a multi-file drop; photos save to the item.
- D2. Build policy for missing photos: placeholder with a warning (default) or block.
- D3. Photo source order when rendering: the item's own photo, else the catalog row photo, else
  placeholder.
- D4. Labels unchanged (barcode only), but may show the thumbnail later.
- D5 (M2b, pending O5). Customer catalog **from your item list**: item picker, your rates as the
  price source, same layout, same photo rules.

### E. Tally push quality
- E1. **Progress reporting and speed** (found in use: batch 1 of 40 sat for minutes). Cause: the
  agent checks in once a minute and takes one queued job per check-in, and each batch is queued
  only after the previous result, so 1,963 items in 40 batches takes 40 minutes or more. Fixes,
  in order of effort: show the real phase ("waiting for the shop's agent", "Tally is processing",
  "Tally not ready: open the company") with elapsed time and an estimate from completed batches;
  raise the batch size to 100; queue the next one or two batches ahead while each stops on its own
  failure; an agent change so it re-polls immediately after finishing a push job (needs the
  agent release anyway).
- E2. **Release the .NET agent** with the `push_items` case (build passes, own tests not run,
  release and signing flow belongs to the delivery-hardening work).
- E3. Preflight keeps its checks; add "estimated time" and a heads-up when over about 10 batches.
- E4. **Selling price to Tally (agreed, A20).** Probe `STANDARDPRICELIST` on the dev Tally first
  (dated entry, alter behaviour), add it to the stock-item XML, and re-push when our price changes
  (a changed-price filter in the Tally dialog; prices change with margin, so a stale price must be
  visible). Send only for items whose price is set.
- E5. Dev Tally cleanup of test data (root group `ZZTEST Catalog`, about 186 test items).

### F. Supplier price history (Phase 3, parkable)
- F1. `supplier_price_point` (tenant, item or product, supplier, source quote or bill, source id,
  price, pack, date). Written on price-list import and bill approval.
- F2. Price-list review shows "last bought at X, quoted Y now" and a changed-price filter;
  this replaces the catalog plan's S6 (re-import price diff).
- F3. Both document types feed `item.last_purchase_rate`.

### G. Platform readiness (small, so the rest does not paint us in)
- G1. Permission matrix recorded for the new actions (promote, push, attach photo, merge match).
- G2. Audit-log entries for promote, push, photo attach and match confirm.
- G3. Concurrent-edit guard on review grids (last-write-wins today; add a version check).
- G4. Background-job runner shared by extraction, outputs and pushes, with a stuck-job sweeper.
- G5. Performance budgets on lists and catalog PDFs kept in tests.

### H. Stock hooks (nothing built beyond the flag)
`item.is_stock`; every document line links to the canonical item; approved quantities and units
stay as stored; any later stock view is a read-only comparison with Tally's closing stock.

---

## 5. Execution breakdown

Sizes are working days for one developer, including tests and a browser check. Migration numbers
are the next free ones (0041 onward; 0037 belongs to the agent update work, 0039 is the firm-wide item codes change, 0040 the Tally price column).

### M0. Close what is open (2 days) — BUILT 2026-10-05 except the agent release and Tally cleanup
1. E1: honest progress states, ETA, batch size 100, queue-ahead of one batch; backend change plus
   dialog change; tests with the simulated agent for each phase.
2. E1 agent re-poll change, and E2 release of the agent (with the delivery-hardening owner).
3. E5 cleanup note and, if you want, a Tally-side cleanup run.
4. E4: probe and build the selling price in the Tally push (agreed).
Acceptance: pushing 1,963 items shows the real phase within one check-in interval and finishes in
under 15 minutes on a normal shop connection; a stalled agent says so within 2 minutes.

### M1. Item photos, the media foundation (3 days; migration 0041) — BUILT 2026-10-05 except the Firm-page usage line, quota limits and a scheduled sweep
1. A1 and A2: `media_asset`, `item.primary_media_id`, `item.is_stock`; migration, models.
2. A3: upload service with resize and thumbnail, signed URLs, quotas.
3. A6: promote copies the catalog photo; back-fill for items already promoted from catalogs.
4. A4: item list thumbnail, item page upload or camera, replace and remove; mobile check.
5. A5: bulk attach by code or name with a review step.
6. Tests: size and type limits, dedupe by hash, never overwrite on promote, tenant isolation,
   EXIF rotation, back-fill, mobile camera input present.
Acceptance: a promoted catalog item shows its photo in the item list; a user can add a photo to a
bare item from a phone in two taps.

### M2. Catalogs go with a photo (2 days)
1. D1: missing-photo count, filtered view, add photo per item inside the customer catalog dialog.
2. D2 and D3: placeholder policy and photo source order in the PDF renderer.
3. Tests: build with and without photos, placeholder drawn, own photo beats catalog photo, no cost
   or supplier code can reach the renderer (existing guarantee kept).
Acceptance: a catalog never leaves with a blank hole; added photos persist on the items.

### M2b. Customer catalogs from the item list (2 to 3 days; agreed, staged after M2)
1. Item picker (group, search, selection), price source = item rate, same layout and options.
2. Versioning and the "out of date" flag follow item rate and photo changes.
3. Tests and a browser check.

### M3. One front door (2 to 3 days; no migration)
B1 to B3 as above, plus detection tests on the three sample PDFs and flag-off behaviour.
Acceptance: Inward and Catalogs are one place with one upload; old links still work.

### M4. Shared plumbing and one matcher (4 to 5 days; migration 0042)
C1 to C6. Order: supplier resolver, matcher with remembered matches, cross-supplier confirm UI,
group mapping, storage move (copy first), extractor interface.
Acceptance: the same glass arriving on a bill and in a price list resolves to one item; a confirmed
match is remembered; no regression in the full suite.

### M5. Price history (3 days; migration 0043; parkable)
F1 to F3, including the price-change filter and the retirement of S6.

### M6. Platform readiness (3 days, can run alongside M3 to M5)
G1 to G5. Acceptance: every new action is role-checked and audited; concurrent edits warn.

### Order and critical path

```
M0 -> M1 -> M2 -> (M2b) 
        \
         M3 -> M4 -> M5        M6 alongside
```
M1 is on the critical path for everything photo-related; M3 is independent and can start in
parallel with M1 if two people work. Total about 19 to 24 working days.

---

## 6. Data model changes (summary)

| Change | Milestone |
|---|---|
| `media_asset` (new); `item.primary_media_id`; `item.is_stock` | M1 |
| `item_alias` extended or reused for cross-supplier remembered matches; supplier-line mapping gains `item_id` earlier | M4 |
| `supplier_price_point` (new) | M5 |
| group mapping store (reuses `item_category` and the Tally group map) | M4 |

---

## 7. Hooks for what is parked

- **Stock:** canonical item link on every line, `item.is_stock`, stored quantities and units; later,
  a read-only comparison with Tally's stock summary.
- **Other documents (purchase order, goods receipt, quotation):** new document types behind the
  extractor and approval plugin points of section 3.
- **Multi-location, roles beyond owner and accountant and viewer:** tenant and user scoping already
  in place; permission matrix from G1 is where they attach.
- **Other media (party logo, attachments):** `media_asset` owner type.

---

## 8. What blocks on which open decision

| Open | Blocks |
|---|---|
| O1, O2 | M3 naming and detection UI |
| O3 | the shape of M4 (lazy item creation or not) |
| O6, O7, O9 | M1 and M2 details; defaults are safe to start with |
| O8 | whether M5 stays in this plan |

Start M0 and M1 now; neither depends on an open question.

---

## 9. Verification strategy (every milestone)

- Backend: the existing suite plus new tests per milestone; the full suite before hand-off.
- Migrations: DDL checked with `alembic --sql` against Postgres syntax; back-fills tested against
  legacy-shaped data.
- Live Tally: the push work is verified against the dev TallyPrime "Fleek" with the agent
  simulator and, for M0, the real agent.
- Browser: desktop and mobile checks for every UI change, with screenshots reviewed.
- No commits or pushes by us; the user checks in.

## 10. Risks

| Risk | Handling |
|---|---|
| Photos balloon storage and slow lists | resize on upload, thumbnails, dedupe by hash, quotas |
| Merging two document types hides real differences | separate tables and approval rules stay; only front door and plumbing merge |
| Cross-supplier matching merges different products | always a suggestion a person confirms |
| Push over a slow agent frustrates users | M0 progress, ETA, larger batches, agent re-poll |
| Agent release is outside this plan's control | tracked as E2 with its owner; the backend degrades to slower batches meanwhile |
| Scope creep into stock or POs | section 7 only; anything more is a new plan |

---

## 11. Media storage at scale (design target: up to 1,000,000 images per firm)

Measured on the sample catalog: 2,058 photos averaging **14.7 KB** (about 390 x 260 px), 30 MB
in total. A million of those is about 15 GB. The real risk is not the supplier photos but
**uncontrolled uploads** (a phone photo is 1 to 4 MB, so a million is 1 to 4 TB) and the output
files (a 2,056-item customer catalog PDF is 32 MB, and versions pile up).

Rules, all part of M1 (media foundation):

1. **Normalise on ingest, never store the raw upload.** Cap the long edge at 1,000 px, strip EXIF
   (after applying rotation), re-encode as WebP (quality about 78). Make one **thumbnail** (240 px
   WebP, about 6 KB) for lists. Target about 40 KB for the main image and 6 KB for the thumb.
   Limits: 10 MB upload, 40 megapixel cap (decompression-bomb guard), jpg/png/webp/heic only.
2. **Content-addressed, deduplicated per firm.** Key `media/{tenant}/{sha256}`; many rows (catalog
   rows, items, re-uploads of the same PDF) point at one object. Today's key includes the catalog
   id, so a re-upload stores every photo again; this fixes that. A photo belongs to the item or row
   by reference, so deleting a catalog never deletes a photo an item still uses.
3. **Process off the request.** Resize and encode in the job runner; the row shows the original
   until the renditions are ready. Batch imports write in parallel (already done for catalogs).
4. **Serve cheaply.** Immutable, hash-named URLs with long cache headers, behind the CDN in front of
   R2; lists use the thumbnail; the API stops proxying image bytes. Signed, hour-stable URLs stay
   for private tenants.
5. **PDFs embed a print rendition,** not the full image: about 600 px JPEG, transcoded once per
   build. This should cut the 32 MB customer catalog substantially.
6. **Retention for what is derived:** keep the latest 3 customer-catalog versions and mark older
   ones expired; label output files stay at 7 days (done); the source supplier PDF is kept 90 days
   after import unless the catalog is still in review (it can be re-uploaded).
7. **Garbage collection:** a nightly job removes objects with no reference after a grace period
   (mark and sweep from `media_asset` and catalog rows), and reports bytes freed.
8. **Quota and visibility:** per-firm byte counter from `media_asset`, a usage line on the Firm
   page, a soft limit with a warning and a hard limit on upload. Default sized for the target
   (about 100 GB per firm), adjustable per plan.
9. **Database:** one `media_asset` row per image (about 200 bytes plus indexes, so about 300 MB per
   million) is fine on Postgres; index (tenant, sha256) and the owner columns; no partitioning
   needed. Lists stay on keyset pagination.

Cost (R2 about $0.015 per GB-month, no egress fee): a million normalised images at about 48 KB
(main plus thumb) is about 48 GB, so **under $1 a month per firm**. Without normalising, phone
originals at 2 MB would be about 2 TB, so about $30 a month, and slow to serve. Cross-firm
deduplication of identical supplier catalogs is possible later (reference-counted shared blobs)
but is not planned: per-firm isolation is simpler and the saving is small at this size.

### M0 build notes (2026-10-05)
- **Selling price to Tally (A20) built and live-verified.** Probe on the dev Tally: the item takes a
  `STANDARDPRICELIST.LIST` (`DATE`, `RATE` as `150.00/Nos`); re-sending an item **replaces** the
  list; a date that is not the 1st or 2nd of a month is **silently dropped** on an unlicensed
  Tally (the list comes back empty), while any past 1st-of-month is kept. So every item gets one
  entry with a fixed old date (`20200401`) meaning "the current price", and a changed price is a
  plain re-send. Price source: the catalog's new price for items the catalog created (their
  billing rate is set to match), the item's own rate for items the shop already had. A row
  remembers the price last sent (`tally_price`, migration 0040); a changed price makes a synced
  item due for a price update, and rows synced before this are updated once. Live: 42 items sent,
  42 of 42 prices match; margin 25 to 10 re-sent, 42 of 42 updated.
- **Progress:** the run now reports its phase (waiting for the agent / sending / waiting for
  Tally), start time and an estimate from finished batches; the dialog shows how long it has
  waited and warns after 3 minutes. Batch size is 100.
- **Root cause of the stall, fixed in the agent:** one checkin a minute plus one poll a minute
  meant up to two minutes between batches. The agent now checks in and runs again straight after
  it finishes a job (`RequestFastCycle`); the live run of 42 items took about a second end to end
  with the simulated agent. **Needs the agent release** to reach shops.
- Still open from M0: the agent release (owned by the delivery-hardening work) and clearing test
  data from the dev Tally company, which now also holds about 2,300 stock items.

### M1 build notes (2026-10-05)
- **Design change from the plan:** `media_asset` is one row per *stored image* (unique per firm by
  the hash of the normalised main rendition), not per owner. Things point at it (`item.primary_media_id`
  today). That gives the dedupe for free and makes "is it still used" a query. Migration 0041 also adds
  `item.is_stock` (unused, hook for stock).
- **Ingest** (`services/media.py`, Pillow now a declared dependency): reject empty, non-image, GIF/BMP/other
  formats, over 10 MB, over 40 megapixels; apply EXIF rotation then drop metadata; flatten transparency
  onto white; main rendition 1,000 px long edge WebP q78, thumbnail 240 px q72; small photos are not
  enlarged; keys `media/<tenant>/<sha>.webp` and `_t.webp`. HEIC is not read on the server: the file
  inputs list only jpeg/png/webp so iOS converts before upload.
- **API:** `POST/DELETE /api/items/{id}/photo`; `POST /api/items/photos/stage` (up to 200 files,
  processed and matched by file name to an item code or exact name, nothing attached) then
  `POST /api/items/photos/apply` (reviewed pairs; another firm's photo or item is skipped);
  signed `GET /api/media/{id}/{photo|thumb}` (no bearer header needed, so `<img>` works);
  `GET /api/media/usage`. `ItemListItem` and `ItemOut` gain `photo_url` and `thumb_url`.
- **Promote copies the catalog photo** in a background task (own session, 50 at a time, never over an
  item's own photo, same picture stored once). Verified: 23 catalog items got their photos; an
  average of about 16 KB per image (main plus thumbnail) in the sample.
- **Web:** photo block on the item page (Take photo with the camera, Choose or Replace, Remove, click to
  zoom), thumbnail in the flat items list, an "Add photos" dialog on the Items page with a review step
  (change or skip each match), and a note in the promote dialog while photos are copied.
  Checked in a browser on desktop and mobile.
- **Tests:** 23 new (normalise, limits, EXIF, transparency, dedupe, tenant isolation, replace/remove, bulk
  stage and apply, usage, orphan sweep, promote copy, own photo never replaced).
- **Not done yet:** the usage line and soft/hard limits on the Firm page (the endpoint exists);
  running `sweep_orphans` on a schedule (there is no scheduler in the repo, so it is a function with a
  test); thumbnails in the item tree view; photos on parties or documents.

### Items-hub layout (2026-10-06, replaces the old M2, M2b and the front-door part of M3)
Supplier documents bring items in; Items does everything with them (selection bar: Make customer
catalog, Print labels, Send to Tally, Add photos, Set availability, Edit prices and groups); a
Customer catalogs tab under Items lists what was made. Click counts for the common paths (price
list to customer catalog): first time 12, repeat 8 (6 when the supplier is set to mark in stock when
adding), catalog from items already in stock 5, missing photos in bulk 4, inward bill into items 5.
Order: Items hub (pack size, filters, selection bar, came from, availability, Import items menu);
customer catalogs from items; labels from items; Tally push from items; clean-up and supplier
documents front door. Exports and Excel round trip, photo queue and changed-rows-only re-import
follow. Nothing built yet.

---

## 12. Gap register (2026-10-06): every known gap, so none is lost

Sequence is free as long as each gap closes. Status: open, done, or parked on purpose. "H" is the
Items-hub work (section 11 layout); "MP" is the shared matcher and supplier-documents front door.

| # | Gap | Closes in | Status |
|---|---|---|---|
| G1 | Deleting an item that came from a catalog leaves product and catalog-row links dangling (an FK error on Postgres); merging items does not repoint them | H1 | DONE 2026-10-06 |
| G2 | No availability status; price-list items must start Out of stock, bills In stock, approve sets In stock | H1 | DONE 2026-10-06 |
| G3 | Items lack pack and carton size (needed for "for 7 pcs" and per-piece price on any catalog) | H1 | DONE 2026-10-06 |
| G4 | No filters on Items for no photo, availability, source supplier or document, in Tally | H1 | DONE 2026-10-06 |
| G5 | Bulk edits on Items are by ticked ids only; need "everything matching this filter", a count preview, undo | H1 | DONE 2026-10-06 |
| G6 | An item does not show where it came from (price lists, bills), nor offer "Import items" | H1 | DONE 2026-10-06 |
| G7 | Customer catalogs are built from supplier-catalog rows; must be built from Items, firm-level, with an Items tab listing them | H2 | done (H2) |
| G8 | Missing-photo flow at build time (count, add photo per item, placeholder or wait) | H2 | done (H2) |
| G9 | Customer catalog "out of date" must follow item price, name, photo and availability | H2 | done (H2) |
| G10 | Customer catalog includes only In stock by default; Expected optional; "on order" label optional | H2 | done (H2) |
| G11 | Labels are built from catalog rows; must come from Items | H3 | done (H3) |
| G12 | Tally push state (status, price sent) lives on catalog rows; must live on items so bill items can go too | H4 | done (H4) |
| G13 | Tally price is overwritten from the catalog's price at push time; under item-first the item's own rate is the truth | H4 | done (H4) |
| G14 | Reading the price back from Tally after a push, since an unlicensed Tally drops bad dates silently | H4 | done (H4) |
| G15 | Supplier defaults (margin, rounding, group map, add automatically, mark in stock) | H5 | done (H5) |
| G16 | Review by exception: queue chips, Accept clean rows, one-click Add all included with an Undo toast | H5 | done (H5) |
| G17 | "Update item prices" after a margin change (explicit, with a count) | H5 | done (H5) |
| G18 | Replace photo on a catalog row; "photo needs checking" filter; products with no photo in the PDF are not picked up at all | H5 | done (H5 + 2026-10-06): manual replace; automatic flag for small, odd-shaped, blank crops with an It-is-fine action; price lines with no picture are counted and reported (those products still need adding by hand: reading a product with no picture is not built) |
| G19 | Photo queue, camera mode, barcode-scan lookup, zip or folder drop, paste | H6 | done (H6): Photo queue (camera, file, paste, scan a code); zip/folder drop already existed; item search now finds by code prefix |
| G20 | Excel export and import of items, updating by code | H6 | done (H6): Export to Excel / Update from Excel (xlsx or csv, matched by code, dry run first) |
| G21 | Background job indicator (imports, photos, builds, Tally) in one place | H6 | done (H6): header pill + /api/jobs/active (label prints, customer catalog builds, Tally send); desktop header only |
| G22 | Item names in bulk: variant attribute instead of an appended code, find-and-replace rules | H6 | partly (H6): bulk find-and-replace in names built; variant attribute instead of an appended code NOT built |
| G23 | HSN and GST default per group, inherited by new items | H6 | done (H6): HSN and GST default per group; inherited by new and added items; Apply to items without HSN |
| G24 | Shared item matcher so a bill line reuses a price-list item (no duplicates); supplier code, confirmed matches remembered | MP | done (MP): a bill from a supplier links to that supplier's own price-list product by code or name (method code); approving remembers fuzzy/manual wordings as aliases and records price history |
| G25 | Bill approval: price-vs-quote flag, discontinued item exception, part deliveries | MP | partly (MP): above-quote and discontinued flags on bill lines built; part deliveries NOT built (there are no purchase orders to deliver against) |
| G26 | Supplier documents front door: one nav, supplier-first upload, "No supplier" choice, type detection, Documents tab complete | MP | partly (MP): Supplier documents front door (one nav, detect with override, supplier picker or no supplier, one list, supplier Documents tab with bills + spend) built; moving inward PDFs onto CatalogStorage and one shared supplier resolver NOT done |
| G27 | Re-import shows only what changed; supplier price history (quote versus paid) | MP | done (MP): supplier_price_point history; a re-import marks new/up/down/same against the last list with a Price changed filter; last bought at on each row |
| G28 | Agent release carrying `push_items` and the fast-poll fix | outside (delivery-hardening owner) | open |
| G29 | Dev Tally "Fleek" holds about 2,300 stock items from tests | user | open |
| G30 | Firm-page photo usage line and limits; scheduled orphan sweep (no scheduler exists); item-tree thumbnails | H6 | done (H6): photo usage line on the Firm page, daily in-process sweep (also removes print copies), thumbnails in the item tree |
| G31 | Platform readiness: role matrix and audit entries for the new actions, concurrent-edit guard on review grids | H6 | done (H6): audit entries for the new bulk/outward actions, role-matrix test, stale-edit guard on price-list rows |
| G32 | Migration 0039 and the others not yet run on a real Postgres; full backend suite not rerun after the latest changes; nothing committed | before release | open |
| G33 | Catalog sharing on WhatsApp to customers | parked | parked |
| G34 | Stock quantities and reconciliation (Tally stays the truth; hooks only) | parked | parked |
| G35 | Cover customisation for customer catalogs | parked | parked |

Order used: H1 (Items hub foundation), H2 (customer catalogs from items), H3 (labels), H4 (Tally from
items), H5 (supplier defaults, queues, catalog-page fixes), H6 (bulk tools, polish, platform), MP
(matcher and front door) can run in parallel with H5 and H6 since it does not depend on them.

### H1 build notes (2026-10-06): Items hub foundation (migration 0042)
- **Availability** on items (In stock, Expected, Out of stock, Discontinued), **pack and carton size**.
  Promote creates items Out of stock with the row's pack and carton size; linking to an existing item
  keeps its availability and teaches it the pack size; approving an inward bill sets its items In stock
  (a Discontinued item stays Discontinued: it is not silently revived).
- **Filters** on `GET /items`: availability (several), no photo, in Tally, came from a supplier or price
  list; chips with counts on the Items page.
- **Selection by filter:** `ItemFilter` for bulk edit and bulk delete (exactly one of ids or filter, up to
  20,000 matches), `POST /items/count`, "Select all N matching" in the selection bar, dry-run preview
  capped at 200 shown rows ("and N more"), new "Set availability" action, pack and carton size in Edit fields.
- **Came from:** `GET /items/{id}/sources` and a block on the item page linking to the price lists and
  bills behind the item. **Import** menu on the Items page (price list, bill, Tally, by hand).
- **G1 fixed:** deleting an item (single or bulk) clears the price-list product and row links; merging
  repoints them and passes the loser's photo to a winner without one.
- **Open in Items** button in the Add to items dialog opens the Items list filtered to that price list.
- **Tests:** 15 new (`test_items_hub.py`, `test_availability_on_approve.py`); catalog, inward, item,
  category, party suites green. Browser-checked: add 100 items, Open in Items, chips, Select all 100
  matching, Set availability preview and apply, item page with Came from, Import menu.
- **Seen while testing, still open:** names that clash get the item code appended in brackets (G22).


### H2 build notes (2026-10-06): customer catalogs from Items (migration 0043)
- Customer catalogs are firm-level, built from an Items selection (ticked ids or "all matching" filter). Selection, layout and member ids are stored; "out of date" is computed on read (item changed, deleted/archived, or filter membership changed). Rebuild = next version in the same series; a filter is re-evaluated.
- API: `/api/customer-catalogs` (`check`, create 201/202, `rebuild`, `preview`, `jobs/{id}`, list, `file`, delete). Old supplier-page customer-catalog code and endpoints removed.
- Web: Items selection bar "Make customer catalog" (flag-gated) -> `MakeCatalogDialog` (check panel with left-out counts, per-item "Add photo", placeholder or block, include-expected + "On order" label, layout remembered); `/items/catalogs` list page (open, new version, delete, out-of-date badge).

- Browser-verified end to end (select items -> check -> add photo -> create -> list -> out of date -> new version); catalog+inward suite 487 pass. Fixed a dev local-storage temp-file race (unique tmp name).

### H3 build notes (2026-10-06): labels from Items
- `POST /api/item-labels` (`check`, create 200 PDF / 202 job, `preview`, `jobs/{id}`, `jobs/{id}/file`); service `services/catalog/item_labels.py`; 7 tests. Barcode = item `sku` (else `barcode`); price = item selling rate. Items without a code get the next firm-wide number when printed (tick-box, on by default; off leaves them out). Archived/merged items left out. Not tied to availability.
- Web: Items selection bar "Print labels" -> `LabelsDialog` (now items-based, shows code assignment and no-price notes). The supplier-catalog page no longer has Print labels. Old `/supplier-catalogs/{id}/labels` endpoints still exist, unused (remove in a clean-up).
- Browser-verified (10 items, codes assigned, PDF ready).

### H4 build notes (2026-10-06): Tally push from Items (migration 0044)
- State on the item: `tally_status` (none/queued/synced/error), `tally_price` (last sent), `tally_pushed_at`, `tally_seen_price` (what Tally showed at the last check; 0 = no price there). Computed `tally_state` on the item list: none, queued, error, imported (has a Tally id), synced, price_due (item rate changed since sent), price_differs (Tally shows another price: read back, so a dropped price list is caught).
- Price truth is the item's own selling rate: the old "catalog price overwrites item rate at push" is gone. A margin change on a price list no longer reaches Tally until the item prices are updated (H5, explicit "Update item prices").
- API `/api/item-tally` (`preflight`, `push`, `run`), one push at a time per firm; settings and "check Tally" stay under `/supplier-catalogs/tally/*`. Items with no code get one when sent (it is the Tally part number). A failed or cancelled price update leaves an item marked in Tally.
- Parser reads the latest `STANDARDPRICELIST` rate (`selling_price`); the post-push reconcile check stores it.
- Web: Items bar "Send to Tally" (TallyDialog, now selection-based), list badge for price old / differs / error / sending, "Tally price old" filter chip with count. Send to Tally removed from the supplier-catalog page (it keeps "Add to items").
- Tests: tally suite ported (43 pass) incl. read-back, hand-added item, price-due filter; full backend suite passes. Browser-verified with a fake agent (check, send, badges).

### H5 build notes (2026-10-06): supplier defaults, review by exception (migration 0045)
- `supplier_catalog_default` per supplier: margin, rounding, group map (rules' suggested group -> your group name), add automatically, mark in stock. `GET/PUT /api/supplier-catalogs/suppliers/{party}/defaults`. Applied at upload: margin + rounding then reprice, group map in the importer, then (if set) promote all included and mark In stock, photos copied in the background. Edited on the party's Catalogs tab; "Use this margin for this supplier's next catalogs" on the price bar.
- `POST .../{catalog}/prices/preview|apply`: explicit "Update item prices" with a count (items in your item list whose rate differs from the price list's selling price). `promote` now returns `created_item_ids`, takes `mark_in_stock`; `POST .../promote/undo` removes only untouched new items (not on a document, not billed, not sent to Tally), in batches.
- Catalog page: "To review" chips (Need a group / Possible matches / Not in your items, with counts), "Add all included to items" with an Undo link, "Update item prices...", Replace/Add photo on each row (also updates the item's photo). New `not_added` row filter. Row image URLs carry a `v=` cache-buster.
- NOT done (G18 remainder): flagging crops that look wrong ("photo needs checking") and products with no picture in the PDF, which the extractor still skips. "Accept clean rows" was not built: the chips plus one-click add cover review by exception without a separate accepted state.
- Tests: 10 in `test_supplier_defaults_h5.py`; full backend suite passes. Browser-verified (defaults card, chips, add all, undo, update prices).

### H6 and MP build notes (2026-10-06)
- Migrations 0046 (category HSN/GST), 0047 (supplier_price_point, bill line note flags), 0048 (photo flag). `openpyxl` added to pyproject (install it on deploy). 0039-0048 generate clean Postgres SQL offline (`alembic upgrade 0038:0048 --sql`) but were never executed on a real Postgres: no Postgres server or running Docker on the dev box.
- New API: `/api/item-sheet` (export, import), `/api/jobs/active`, `/api/documents` (detect, by-supplier), `POST /api/items/bulk-rename`, `POST /api/item-categories/{id}/apply-hsn`.
- Daily photo sweep runs in the API process (asyncio task in the app lifespan, off in tests): there is still no real scheduler.
- Still open: G22 variant attribute; G25 part deliveries; G26 inward storage move + one supplier resolver; reading price-list products that have no picture; G28 agent release (delivery owner); G29 dev Tally cleanup (you); G32 run migrations on a real Postgres and commit (you); parked G33-G35.

## Closeout (2026-10-06)

**Done:** every workstream above, including supplier documents (front door, supplier-code bill matching, price history). Production fixes found after deploy: price-history rows blocked deleting a merged product; two rows in one catalog could not both take the same product; HSN values must be in the HSN list (all fixed, with tests that now run on SQLite with foreign keys on).

**Parked by decision**
- **G35 customer-catalog cover customisation** (parked 2026-10-06). The cover is fixed: firm name, title, contact. If reopened, the likely asks are a logo, a "valid till" date, a terms line and a short message; none needs a data-model change beyond a few tenant/catalog fields.
- G33 WhatsApp catalog sharing, G34 stock quantities and reconciliation (Tally stays the truth).

**Dropped, with reasons**
- Inward PDFs onto shared storage: the PDF is deleted at approve and the XML is the lasting file, so the move is risk without benefit.
- One shared supplier resolver: price lists pick the supplier by hand and new suppliers already go through the party duplicate rules; bills read it from the PDF. They do not overlap.

**Open, with what each needs**
- G25 part deliveries (supplier side): would need a supplier purchase-order concept, which does not exist. Parked; it is not the next direction.
- PDF products with a price line but no picture: counted and reported on upload, not read. Needs a real sample PDF that has them (none of the real samples do).
- G28 agent release (delivery owner), G29 dev Tally cleanup (user), a live-Tally run of "Send to Tally" from Items (only a fake agent so far).

**Next (user direction, 2026-10-06): strengthen billing, meaning sales invoices to customers (not supplier bills), and ordering, before any more catalog work.** "Ordering" is customer ordering: a unique catalog URL, the customer picks items and places an order, and an invoice is created. Planned in `PLAN-customer-ordering.md` (not built). Known candidates: GST billing (the `gst_enabled` flag is still decorative); multi-user roles and concurrent draft edits (a junior can bill, push to Tally and reverse); handwritten-bill capture; speech entry.

**Lessons for new work**
- The test database enforces foreign keys now. Every delete or merge must repoint or clear whatever references the row; a unique rule that two rows can collide on needs a pre-check that returns a clear message, and bulk actions must skip and count, not fail.
- An item's HSN is a lookup (foreign key): validate against the list, never write free text.
- Migrations 0039-0048 ran fine on the real deploy; the dev box has no usable Postgres, so check SQL with `alembic upgrade A:B --sql`.


# Items page: closing the UI gaps

Drafted 2026-10-08 from a read of `ItemsPage.tsx`, `ItemTree.tsx`, `GroupForm.tsx`, `SelectionBar.tsx`,
`BulkPanel.tsx` and the item filter code. Nothing here is built. Not yet clicked through in a browser.

## What the audit found, and the one fact that shapes the plan

Every "which items" feature (list, counts, bulk edit, rename, delete, make catalog, share link, labels, Tally push,
export, photo queue) already takes a **selection = ticked ids, or an `ItemFilter`**. The filter is one server
function, `filter_clauses` in `api/app/services/item_filter.py`, mirrored by `ItemFilter` in
`web/src/lib/types.ts`. The filter has **no category or group field**, and the tree view has **no selection**.

So the main gaps (no bulk actions in the tree, no catalog for a group) are mostly one missing filter field plus
UI wiring, not new backend features. Doing that first unlocks most of the rest.

Facts checked that change the scope:

- Backend already has item merge (`POST /items/{id}/merge`), category merge, group create and group delete.
  The web calls none of them. These are UI-only slices.
- Customer catalogs are generated PDF jobs (`/api/customer-catalogs`: check, create, rebuild, list, file,
  delete). There is **no "add items to an existing catalog"**. The earlier idea of that is dropped; the
  nearest thing is Rebuild, which is a different feature.
- `/items/tree/leaves` already selects by `group_id`, `category_id` (loose) or `uncategorised`.
  The new filter fields must mean the same thing, so the tree and flat view never disagree on a count.
- `GroupForm` has a leftover "Mode" column header (retired `rate_mode`) with no matching cell, and sizes open
  with `window.location.assign` (full page reload).

## Decisions I am proposing (change any you disagree with)

1. **Selection model:** `ItemFilter` gains `group_id`, `category_id` and `ungrouped` (loose leaves in a
   category, or uncategorised when `category_id` is empty). A tree node's actions send the filter, not a list of
   ids, so a group of 800 sizes works and no tick limit applies (the 20,000 filter cap still does).
2. **Tree gets selection, not a second action system.** Tree nodes get a checkbox and a ⋯ menu; both feed
   the same `SelectionBar` and the same dialogs. No new dialogs for the tree.
3. **Selection survives switching Tree and Flat**, held as either ids or one tree-node filter.
   Today it is wiped on every view change.
4. **Destructive group actions go through the existing preview-then-apply pattern** in `BulkPanel`.

## Slices

Each slice is shippable alone, has backend tests where the server changes, and ends with a click-through.

### G0. Group and category in the filter (backend and web types)

- `ItemFilter` (schemas_item.py): `group_id`, `category_id`, `ungrouped: bool`.
- `filter_clauses`: `group_id` means `Item.group_id == x`. `category_id` alone means every item in the category
  (grouped or not). `category_id` plus `ungrouped` means loose leaves only (matches the tree's "Ungrouped"
  row). `ungrouped` with no category means uncategorised and ungrouped.
- `GET /items` and `POST /items/count` accept the same fields (the list route builds its own `ItemFilter`
  from query params, so add them there too).
- Web: extend `ItemFilter` and `buildQuery`; add a category/group chip row to the flat view so the filter is
  reachable without the tree (a picker, and a removable chip like "From a price list ✕").
- Tests: add to `test_item_hierarchy.py` / `test_items_bulk.py` for each combination, and one that checks a
  tree node count equals the filter count.
- Exit: flat view filtered to "Bartan > Thali" returns the same items as expanding that group.

### G1. Tree actions (the headline gap)

- Tree gets checkboxes on category, group and leaf rows. Checking a category or group selects by filter
  (indeterminate state when only some leaves are ticked); checking leaves selects ids.
- ⋯ menu on category and group rows: Make customer catalog, Share link, Print labels, Send to Tally,
  Set availability, Edit fields, Move, Export to Excel. Each opens the existing dialog with a filter selection.
- `SelectionBar` renders in tree view too. Lift `selected`, `allMatching` and the dialog state out of the flat
  branch of `ItemsPage` (it is currently reachable only when `view === "flat"`).
- Selection count for a filter node comes from `/items/count`; show it before the user confirms.
- Mobile: tree rows keep a 44px hit area; the ⋯ menu is a bottom sheet, not a hover menu.
- Exit: from the tree, make a customer catalog from one group in three clicks, and the PDF contains exactly
  that group's active items.

### G2. Group page actions and cleanup

- Actions strip on `GroupForm`: the same set as G1 applied to this group's filter, plus Add size (creates an
  item pre-set to this group), Ungroup all, Delete group (existing `DELETE /item-groups/{id}`; show how many items
  it will ungroup first), Merge group into another (new endpoint, see risks).
- Remove the dead "Mode" header; make the size grid header match its three columns.
- Size links use the router (`nav`), not `window.location.assign`.
- Category node gets a detail pane too (name, HSN default, counts, actions), since today clicking a category only
  toggles it open.

### G3. Missing bulk actions

- **Bulk price change:** set rate, or raise/lower by % or ₹ with rounding; preview shows old to new per row.
  The server dry-run pattern already exists in `BulkPanel`; add a mode (`price`) and a server op because
  "adjust by %" cannot be expressed as a single value in `ItemBulkUpdate`.
- **Bulk archive / unarchive** as its own button (today archive only appears as a fallback inside Delete).
- **Bulk confirm** for unconfirmed items.
- Group the `SelectionBar` into: Edit (availability, fields, rename, move, price), Output (catalog, share,
  labels, export), Tally, and a ⋯ overflow with Archive and Delete. Nine buttons in one wrapped row do not
  work on a phone.
- Verify while building: whether "Edit fields" already covers HSN (`hsn_code` is bulk-editable on the server).

### G4. Merge and housekeeping UI (backend exists)

- Merge item: from the item page ("This is a duplicate of…" with an item picker), showing what moves
  (invoice lines, aliases, photos) before confirming. Surface merged-away items as a redirect, not a 404.
- Merge category: in `CategoryManager`.
- Duplicate item (clone with a new name) and a "Used in" panel: catalogs and share links that include the item,
  plus billed count. Check whether price history already has an item-level endpoint before building one.

### G5. Flat and tree usability

- Tree: a search box that auto-expands matching nodes; expand-all / collapse-all; remember open nodes in
  `sessionStorage`; show availability and Tally state on leaves like the flat row does.
- Flat: scope chips become combinable (Unconfirmed + No HSN), a sort control, supplier chip (`supplier_id` is in
  the filter but only `?catalog=` is wired), "Select all matching" offered up front instead of after the first tick.
- Export: label says what it exports (ticked rows, or N matching), and the `allMatching` case uses the filter
  (today a "select all matching" export sends the ticked ids from the loaded page only).
- Header: group the seven rail buttons (Import, Photos, Photo queue, Catalogs, New) into New / Import /
  Photos menus; move Export out of the Import menu.
- Page width: lift the `max-w-5xl` cap on desktop so the flat list can show columns (rate, UOM, availability,
  Tally, photo). A real table is a follow-up; do not start it here.

### G6. Drag and drop in the tree (last, optional)

- Drag a leaf into a group, a group into a category. Reuses the bulk move endpoint with a single id. Keyboard
  alternative required (Move… in the ⋯ menu from G1 already covers it), so this is polish only.

## Order and rough size

| Order | Slice | Server change | Size |
|---|---|---|---|
| 1 | G0 filter fields | yes, small | S |
| 2 | G1 tree actions | none | M |
| 3 | G2 group page | merge-group endpoint | M |
| 4 | G3 bulk actions | price op | M |
| 5 | G4 merge UI | none | S |
| 6 | G5 usability | none | M |
| 7 | G6 drag and drop | none | S |

G0 to G2 answers both examples the user gave (bulk actions in the tree; catalog for a group of items).
Suggest committing after G1 and again after G3.

## Risks and things to check before building

- **Counts disagreeing.** The tree's `leaf_count` counts active leaves (`_active_item`), while a filter with
  `status=None` excludes only archived. Make the two share one definition in G0 or the group badge and the
  "N selected" count will differ by the unconfirmed or merged rows.
- **Big selections.** Filter-based selection resolves every id server-side (`resolve_ids`, cap 20,000). Fine
  for groups; a whole category on a large catalog will hit the cap, and the error text should say so plainly.
- **Tally and delete.** Bulk delete of a group's items must keep today's rule (items on documents are archived,
  never deleted). "Delete group" must not delete items, only ungroup them. Re-read
  [[catalog-arc-closeout-2026-10]] for the FK and unique-rule traps before touching delete or merge.
- **Merge group** has no endpoint; define what happens to size order, HSN and name clashes before building.
- **Catalog module gate.** Catalog, share, labels and Tally buttons are gated on `me.ext_supplier_catalog`;
  keep that gate on the tree menus too.
- **Role gate.** Item writes are `WriteUser` (owner, accountant). The counter role should see the tree and
  filters but not the action menus (consistent with [[billing-hardening-plan]]).
- **Prod versus SQLite.** The search path uses `word_similarity`; new filters must not touch it. Run pytest
  with `DATABASE_URL=sqlite:///./_mytest.db`.
- **WhatsApp cost rule.** "Share link" is free; do not add any "send catalog by WhatsApp" action to these
  menus (see [[whatsapp-promotional-cost-rule]]).

## Verification

- Backend: new tests per slice; full suite green; `mypy`/`ruff` as in the repo.
- Web: typecheck and lint clean, then a browser click-through on a desktop and a phone-width window for each
  slice (tree check, ⋯ menu, selection survives Tree to Flat, bulk preview and apply, group delete).
- A short visual review page in `docs/visual-plan/` for G1 and G3 before coding the UI, as in earlier slices.

## Open questions for the user

1. Are G0 to G2 the right first release, or should bulk price change (G3) come before the group page?
2. Should counters see the tree and filters read-only, or the Items page not at all?
3. Merge group: wanted now, or leave it out until someone asks?

## Build notes (2026-10-08)

All slices built, **uncommitted, not yet clicked through in a browser**. Backend: full suite passes (new file
`api/tests/test_items_page_gaps.py`, 10 tests); web: `typecheck` and `lint` clean. No migration.

**Server**
- `ItemFilter` gained `group_id`, `category_id`, `uncategorised`, `ungrouped` (`services/item_filter.py`); `GET /items`
  takes the same query params. Tree counts and filter counts agree (a test checks it), so the "counts disagreeing"
  risk did not materialise: both exclude archived and merged and include unconfirmed.
- `POST /api/item-groups/{id}/merge` (items, aliases and classify rules move; items take the target's category and are
  appended after its last size). `PATCH /item-groups/{id}` with a new `category_id` now carries the group's items with it.
- **Latent bug fixed:** `DELETE /item-groups/{id}` would have failed on Postgres when a classify rule pointed at the group
  (no ON DELETE; SQLite does not enforce it). Rules are now detached, not blocked.
- `POST /api/items/bulk-price` (percent or amount, raise or lower, optional rounding, dry run). Band warnings are shown
  in the preview, not blocked. Tree leaves now carry `availability` and `tally_state`.

**Web**
- Tree: checkboxes (leaf = that item; category / group / Ungrouped = the whole node as a filter; tick several and they act together as "any of these", via `ItemFilter.any_of`),
  ⋯ menus (bottom sheet on a phone), search, Open all / Close all, open state kept in sessionStorage, availability and
  Tally badges on leaves, drag a size onto a group or category and a group onto a category.
- Selection bar: Set availability up front, then Change / Share / More menus (price, fields, rename, move, confirm,
  archive or restore, catalog, share, labels, Tally, delete). Works in Tree and Flat; ticks survive a view switch.
- Flat: category and group selects, combinable scope chips (BULK/MRP and Unconfirmed/Archived stay exclusive), "Select
  all N matching" offered up front, supplier chip, Export button that says what it exports, Import / Photos menus,
  page widened to `max-w-7xl`. Viewers see no checkboxes, menus or write buttons.
- Group page: + Add size (opens New item inside the group), set availability, make catalog, full node menu, merge,
  ungroup, delete group; dead "Mode" header removed; size links use the router.
- Item page: Copy, Merge into another item, Archive / Restore, merged-away banner, document count.
- Categories: Merge button.
- Bulk panel: Change price, Archive / Confirm / Restore, HSN in Edit fields.

**Not done, on purpose**
- **Sort control** in the flat list: the list is keyset-paged in a fixed order, so a sort needs a server change per
  sort key. Not worth a half-measure; a real columned table is the better follow-up.
- **"Used in" panel for share links and customer catalogs:** their selections are stored as ids or filters, so finding
  the ones that include an item needs resolving each. Only the document count was added.
- A separate category detail page: the category row's menu links to Categories, which now has Merge.
- My earlier audit said "Export ignores the selection when select-all-matching is on". That was wrong: it sent the filter.

- **Several categories / groups at once** (added later): `ItemFilter.any_of` is a list of tree nodes OR-ed together, then ANDed with the rest of the filter. Test: `test_filter_any_of_combines_tree_nodes`.

## Scale check at 10,000 items (2026-10-08)

Measured on a throwaway SQLite database with 10,000 items in one category and one 5,000-item group (a temporary
probe test, deleted afterwards). SQLite has no network hop, so a remote Postgres will be slower per query; the
fixes below remove per-item queries, which is what makes that gap grow.

| Call | Before | After |
|---|---|---|
| `POST /items/count` (runs about 9 times when the Flat view opens) | 480 ms each | 21 ms |
| Tree node of 5,000 leaves | 1 MB, 5,000 rows drawn | 200 first, "Show more" |
| Apply a field change to all 10,000 by filter | 51 s | 1.9 s |
| Move 5,000 items to another group | 38 s | 5 s |
| Delete preview, 10,000 | 6.6 s | 1.1 s |
| Rename preview, 10,000 | 10.9 s | 1.7 s |
| Price change preview, 10,000 | 1.2 s | 1.2 s |
| Export to Excel, 10,000 | 1.7 s | 2.4 s (noise) |
| List page, search | 30 / 55 ms | same |

What changed: counts are one SQL `COUNT`; tree leaves take a `limit` (200, up to 2,000); bulk updates validate the
target group / category / HSN once up front (a bad one is now a single 422, not 10,000 error rows) and write the whole
batch in one flush instead of a savepoint per item; delete counts documents with grouped queries; rename loads the
firm's name keys and synonyms once; ticking more than 500 rows one by one is allowed (the ceiling is now 20,000, same
as selecting by filter).

Not measured / known limits: the Flat list keeps every loaded row in the page (fine for a few hundred; scrolling through
thousands will get heavy, a windowed list is the fix); catalog PDFs, labels and Tally pushes for thousands of items run
as background jobs and were not timed; a 10,000-row preview response is about 1.3 MB (the screen draws 200); the
Flat view's multi-pick sends its nodes in the URL, fine for dozens of picks, not hundreds.

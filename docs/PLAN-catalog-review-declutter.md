# PLAN: Supplier price-list review page, declutter and shorten the path

Status: PLAN ONLY (2026-10-28). Nothing built. Front-end only unless marked **(API)**.
Page: `web/src/pages/catalog/CatalogReviewPage.tsx` (route `/catalogs/:id`), with
`PricingBar.tsx`, `CatalogQueueBar.tsx`, `GroupsPanel.tsx`, `PromoteDialog.tsx`, `MakeCatalogDialog.tsx`.

## 1. What the page is really for (the job)

A price list arrives. The shop owner wants to end up with: **(a)** their own items with their own selling
prices and groups, and **(b)** a customer PDF/link to send. Everything on this page is a step toward that.
The real flow is linear:

1. Check the list read correctly (photos, names, prices).
2. Decide the selling price (margin + rounding).
3. Give every product a group.
4. Resolve "same as an item I already have" matches.
5. Put the products in my items.
6. (Later, once in stock) make a customer catalog / send to Tally / print labels from Items.

The page today shows all six at once, with no sense of order or "what is done".

## 2. Inventory: every control, what it does, and the gap

### Header (right side, 3 to 4 buttons)
| Control | What it does | Gap |
|---|---|---|
| Manage groups | Toggles a panel to rename / merge / delete **shop-wide** categories | Rare, housekeeping, not a per-list step. Same weight as primary actions. Deletes/merges affect the whole shop, not this list, but sits on a list page. Label does not say that. |
| Add to items | Opens PromoteDialog (scope radio: selected / filtered / all) | **Third entry point to the same action** (see "Add all included to items" and the bulk bar "Add to items"). Dialog adds a scope question the user usually does not care about. |
| Make customer catalog (N) | Only shown when N>0 items from this list are in Items. Opens the full layout dialog | Hidden until items are added, so it appears/disappears. Belongs to the *next* stage and the Items page already has it ("Make customer catalog" in SelectionBar). Third route to it (PromoteDialog done-state is the second). Also not offered: a **supplier price list is internal**; the customer catalog is a different document. |
| Delete | Inline confirm, deletes the list | Fine, but a destructive button next to primaries. Belongs in an overflow menu. |

### Pricing card (large, always open, 4 rows)
| Control | Gap |
|---|---|
| Bulk margin % + "₹100 → ₹100" chip | Chip is a good idea, but at 0% it shows nothing useful. |
| Round to | Fine. |
| **Apply to all** | **Misleading label.** It only saves the margin on this price list (the "New price" column). It does NOT touch items. The thing that updates items is "Update item prices…" much further away. User reads "Apply to all" as "apply to my items". Also disabled until dirty with no hint why. |
| 10/15/20/25/30/40/50% chips | Seven chips for a field that takes a number. Shortcut value is low; they cost a row of visual noise. Keep 3 to 4, or fold into the input as a datalist. |
| "Use X% for this supplier's next catalogs" (link, only when a supplier is set) | Useful but buried and a text link. Same setting also lives in SupplierDefaultsCard on the party. Two places to set one thing. |
| "N items have their own margin and ignore the bulk margin. Reset" | Good, keep, but only when N>0 (already so). |
| Static sentence "New price = supplier price + margin, rounded. Supplier prices are never changed." | Permanent text that a repeat user never needs; a tooltip/help line suffices. |

### "To review" bar (chips + 3 to 4 buttons)
| Control | Gap |
|---|---|
| Chips: Need a group 564 / Possible matches 0 / Photo to check / Price changed / Not in your items 763 | The right idea (review by exception). Problems: (1) chips show counts even at 0 ("Possible matches 0") so nothing says "done"; (2) **"Not in your items" is not an exception, it is the add-to-items queue** and mixes with true review chips; (3) chips duplicate three of the five filter dropdowns below (Group=none, Matches=possible), so **two controls drive the same filter and can disagree**; (4) chip filters and dropdown filters are not shown as one "active filters" state. |
| Accept all N matches / Not the same for all | Appear only when N>0, good. But they sit in the same row as the primary action, so a 0-match list still shows a crowded row. Also the bulk bar repeats them as "Accept matches" / "Not the same" and each row repeats "Use it" / "Not the same" (3 levels). Levels 1 and 3 are enough. |
| **Add all included to items** (primary) | The page's true primary CTA. Enabled even while 564 products have **no group**, so you can add ungrouped items (the group chip is "to review" but does not gate anything). Name says "included" though the user cannot tell what "included" means (Included/Excluded pill per row). |
| **Update item prices…** | Valid only *after* items exist and the margin changed. Shown always, even when nothing has been added (dialog then says "0 of 0"). Wrong time, wrong place; next to Add-all it reads as a sibling of the same step. |

### Filters row (5 controls) + bulk bar
- Search, Group, Show (all/included/excluded), Margin (any/item/bulk), Matches (any/possible).
  - **Margin filter (item vs bulk)** is a power filter used only to find overrides; the pricing card already tells you "N items have their own margin" with a reset. Rarely needed on this row.
  - **Matches filter** duplicates the "Possible matches" chip.
  - **Group filter** duplicates the "Need a group" chip (partly).
  - **Show: Included/Excluded** vs a per-row pill vs bulk Include/Exclude: three surfaces for one flag.
- Bulk bar (appears on selection): Set group + Apply, Margin % + Set margin, Clear margin, Exclude, Include, Clear group, Accept matches, Not the same, **Add to items** (4th place), Clear selection. Eleven controls in one row; two of them are input+button pairs. "Apply" and "Set margin" are generic labels.

### Rows
- Included/Excluded pill (status), editable name/price/margin/group inline (good), "It is fine" photo flag, "Replace photo" under every photo even when the photo is fine (a link on every one of 763 rows), barcode drawn in every row (noise; it is the *item's* code, not a review decision), "Use 'X'" group suggestion per row.
- The **"Status" column only says Included** (toggle). It does not say whether the row is already in my items, has a match, or is waiting. That info is hidden in a tiny grey line under the name ("in your items", "in Tally", "new").

## 3. Root causes (why it feels cluttered)

1. **No stages.** Six jobs share one screen, all always visible, none marked done. The user has to infer order.
2. **Same action, many doors.** "Add to items" x4 (header, queue bar, bulk bar, plus the dialog's own scope picker). "Make customer catalog" x3. Accept/dismiss matches x3. Filters x2 (chip + dropdown).
3. **Wrong-time buttons.** "Update item prices", "Make customer catalog", "Manage groups" are shown when they cannot or should not be used yet.
4. **Label mismatch with effect.** "Apply to all" does not apply to items. "Update item prices…" is the real apply.
5. **Page absorbed two roles.** It started as the price-list review, then "Add to items" and "Make catalog" were bolted on. Items page already is the hub for making catalogs, labels, Tally (closeout: "Items are the hub").
6. **No gating or guidance.** Nothing says "563 products still need a group before you add them".

## 4. Target design

Principle: **one primary action at a time, driven by a 3-step progress strip; everything else is secondary or lives where it belongs.**

### 4.1 Header: one primary, one overflow
```
Supplier catalogs / No3 08 Oct Kitchenware ...pdf                 [ Review & add to items ]  [ ⋯ ]
763 items · 65 pages · from <supplier>  ·  Margin 0%, round ₹1 [Change]
```
- Primary button changes with state (see 4.2): `Add 763 to items` -> after adding `View in Items` -> `Make customer catalog`.
- `⋯` menu: Supplier defaults, Rename, Delete. **Manage groups is removed from this page (decided 2026-10-28):** rename/merge/delete of shop-wide categories already live in Items (`CategoryManager`, group page `GroupForm`: rename, merge, ungroup, delete, HSN). Here keep only assigning groups to rows (per row, bulk, suggested) plus a small "Rename or merge groups in Items" link. Delete `GroupsPanel.tsx` and its `showGroups` state in C1.
- Remove the always-visible "Make customer catalog (N)" and "Add to items" from the header.

### 4.2 Three-step strip replaces the pricing card + queue bar buttons
```
① Prices  ✓ 0% / round ₹1  [Edit]    ② Groups  564 to do [Fix]    ③ Add to items  763 ready [Add]
```
- Collapsed summaries by default; clicking a step expands its panel. Pricing card opens automatically only when margin is 0 or when the page is first opened after upload.
- Step 1 Prices: margin input + 3 to 4 quick chips + rounding + live example. Button renamed **"Save prices"** (it saves list prices). Also holds "Use for this supplier next time" as a checkbox, ticked by default when a supplier is set (saves a click and removes the separate link). Item-margin override reset stays here.
- Step 2 Groups: shows "564 need a group". Fix = filters table to no-group rows and opens bulk "Set group". Also "Use suggested group for all" (one click, exists per-row as "Use 'X'") **(API: bulk apply suggested_group)**.
- Step 3 Add to items: the single "Add N to items" button with Undo (existing). Gate: if groups are unresolved, button reads "Add 763 (564 without a group)" with a one-line confirm instead of silently proceeding; setting `Add anyway` stays one click.
- After step 3 completes the strip becomes: `✓ In your items · [Update item prices if margin changed] · [Make customer catalog] · [Open in Items]`. **These later actions appear only here**, i.e. when they are valid.

### 4.3 Filters: one source of truth
- Remove the Matches and Margin dropdowns. Keep Search + one **"Show" menu** with: All, Needs a group, Possible matches, Photo to check, Price changed, Not in items, Excluded, Has own margin.
- Chips become read-only **status counts** in the strip/step headers (and only when >0); clicking one sets the Show filter. No two controls hold the same state; an "active filter: X ✕" pill shows the current one.
- Remove "Possible matches 0" style zero chips.

### 4.4 Bulk bar: 11 controls -> 5 + menu
`N selected  |  Set group ▾  |  Set margin ▾  |  Include/Exclude ▾  |  More ⋯  |  Clear`
- Set group / Set margin each become a small popover (input + Apply inside) rather than an inline input+button pair.
- More: Clear group, Clear margin, Accept matches, Not the same.
- Remove "Add to items" from the bulk bar; the step-3 button takes the selection as its scope automatically ("Add 12 selected to items" when a selection exists, otherwise "Add all N").
- **PromoteDialog scope radios go away.** Scope = selection if any, else all included. Preview/summary becomes an inline confirm under the button (counts: new / matched existing / refreshed) with the existing Undo. Saves one modal + 2 clicks.

### 4.5 Rows
- Replace the "Included/Excluded" Status pill with a real **Status**: `Ready` / `Needs group` / `Possible match` / `In items` / `Excluded`, with Include/Exclude in the row's overflow or by checkbox + bulk. (Excluding is rare; status is what the reviewer scans.)
- "Replace photo": show only on hover/focus, or when the photo is missing/flagged.
- Barcode: drop from the review row (show in the zoom lightbox); it is item identity, not a review decision.
- Keep inline editing (it works well).

### 4.6 Move "Make customer catalog" and "Update item prices" to where they belong
- Customer catalog: canonical home is Items (`SelectionBar`, `NodeMenu`, `GroupForm`). From this page keep **one** entry: the post-add success state ("Make customer catalog from these N items"), which opens MakeCatalogDialog with `catalog_id` filter (already supported). Remove the header button.
- Update item prices: keep the dialog, but show it only (a) after items exist and (b) when the preview says `changing > 0`; surface it as a nudge on the strip ("Margin changed: 120 of your items have an older price. Update") rather than a permanent button.

## 5. Click budget (typical new supplier list, defaults already set)

| Goal | Today | Proposed |
|---|---|---|
| Set margin 20% and add everything to items | type 20 (or chip) -> Apply to all -> Add all included to items = 3 (+ scroll between them) | chip 20% -> Add 763 to items = 2, same viewport; or 0 if supplier default + auto-add |
| Same, but group unresolved first | Need a group chip -> select all -> type group -> Apply -> clear filter -> Add all = 6+ | Fix groups -> (use suggested for all) -> Add = 3 |
| Add then make customer catalog | Add to items (header) -> dialog scope -> Add N -> Make customer catalog -> dialog = 4, or close and find header button | Add N -> "Make customer catalog" in the success strip = 2 |
| Change margin later and refresh item prices | Pricing Apply -> find "Update item prices…" -> dialog -> Update = 4 | Edit margin -> Save -> nudge "Update 120 prices" -> confirm = 3 |
| Accept matches | chip -> Accept all N = 2, or row by row | unchanged (already good); remove duplicate bulk-bar versions |

## 6. Build slices (each independently shippable, front-end first)

- **C1 Header + overflow (low risk).** Overflow menu (Delete, Rename); remove Manage groups + `GroupsPanel`, add link to Items; remove header "Add to items" and "Make customer catalog (N)". Primary action slot = state-driven button. No API.
- **C2 One add path.** Delete duplicate "Add to items" in header and bulk bar; step-3 button uses selection else all included; fold PromoteDialog into an inline confirm + Undo (keep the component's preview endpoint). Wire "Make customer catalog" + "Open in Items" into the success state.
- **C3 Pricing as step 1.** Collapse PricingBar to a one-line summary + Edit; rename "Apply to all" -> "Save prices"; supplier-default as a pre-ticked checkbox; trim quick chips; remove permanent explainer text to a tooltip.
- **C4 Filters unify.** Single "Show" menu (adds the queue states), delete Matches/Margin dropdowns, chips become read-only counts that set the filter; hide zero-count chips. Remove the chip-vs-dropdown state duplication in `CatalogReviewPage` (`matchSel`, `marginSel`, `groupSel`, `notAdded`, `priceMoved`, `photoCheck` collapse into one `view` enum + group select).
- **C5 Bulk bar compaction.** Popovers for Set group / Set margin; More menu.
- **C6 Row status + photo/barcode tidy.** Status column semantic; Replace photo conditional; barcode to lightbox.
- **C7 Update-prices nudge.** Show only when items exist and preview `changing > 0`. Small **(API)** only if we want a cheap count: reuse `/prices/preview` in the strip query.
- **C8 (API, optional) "Use suggested group for all".** Bulk endpoint applying `suggested_group` to rows with no group; reuse `bulk` with a `use_suggested_group` change.

Recommended order: C1 -> C2 -> C3 -> C4 (these remove ~70% of the clutter) then C5-C8.

## 7. Guardrails / traps

- Parity and the "Items are the hub" decision stand: do not re-grow catalog-making on this page; only link to it.
- Keep `expected_updated_at` optimistic locking on inline edits, bulk `target()` semantics (ids vs filter), the `useEffect` that clears selection when the filter changes, and infinite scroll + `/items/count`.
- Add-to-items still fixes codes permanently (PromoteDialog warns); keep that sentence in the inline confirm.
- `promote()` copies `sell_price` into `item.default_rate` only for *new* and *reuse* rows; margin changes later never touch items, which is why "Update item prices" exists. Do not change that; just rename/relocate the entry points so it is understood.
- Visual review before UI coding (see ui-plan-guardrails): produce a static mock of the strip + collapsed pricing + bulk bar first. User does the commits.

## 8. Unrelated problem found while reading the working tree (uncommitted)

`web/src/pages/documents/DocumentsPage.tsx` now has `export default function() {` where it was
`export function DocumentsPage() {`, but `App.tsx:22` imports `{ DocumentsPage }`. That will fail the build
(and the anonymous default export also loses the doc comment above it). Looks like an accidental edit while
adding the upload progress bar.

## 9. Open questions

1. Should "Add to items" ever auto-run on upload when the supplier has `add_automatically` AND a margin default? (Backend exists; the review page could then open directly on step 2 or "Done".)
2. Are excluded rows common enough to keep a visible Include/Exclude control, or only via bulk?
3. ~~Manage groups on this page?~~ Decided: no, handled in Items.

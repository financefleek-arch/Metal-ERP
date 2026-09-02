# Item Categorization — Taxonomy & Build Plan

**Status:** COMMITTED `7958bde` + DEPLOYED & BACKFILLED on prod 2026-09-02. 266 tests pass. Slice fully live.
**Date:** 2026-09-02
**Tenant that prompted this:** SETHIA METAL STORE (Tally "All Masters" export, `c:\tmp\Items list.xml`) — prod tenant id `cbb2c87e-14d0-45bc-8f86-627728ec7d8a`

## What shipped

| File | |
|---|---|
| `app/domain/item_taxonomy.py` | 12 departments, ~112 groups, ~16 brands (13 seeded), ~101 keyword rules, HSN-chapter map. Pure data. |
| `app/domain/item_classify.py` | `classify_item()` — normalise → brand → learned → seed rules → HSN chapter → Other. Confidence: 0.9 branded rule / 0.85 rule / 0.4 HSN / 0.0 none. `>=0.70` ⇒ status confirmed. |
| `app/models/item_classify_rule.py` + `alembic 0010` | per-tenant learned `(phrase → group)` rows. |
| `app/services/catalogue/seed_taxonomy.py` | idempotent: materialises departments + brands + groups. CLI: `--tenant <id>` / `--all`. |
| `app/services/catalogue/classify_apply.py` | `Classifier` (per-tenant, load-once) + `classify_one`. Bridges pure classifier → real `category_id`/`group_id`/`status`. Hybrid: brand-category if the brand exists as one, else department-category. |
| `app/services/catalogue/learn_from_recategorize.py` | recategorise an *unconfirmed* item in Items UI → learned rule (distinctive name phrase, ≤4 words, stop-words + sizes dropped). |
| `tools/reclassify_items.py` | backfill CLI. `--out CSV` dry-run, `--apply` writes. Policy: always set category+group; high-conf ⇒ confirmed; `--keep-status` / `--all-unconfirmed` override. |
| `tests/fixtures/sethia_classify_golden.csv` | 2,094-row regression fixture (`test_golden_matches_current_classifier` guards edits). |

**Wired into all 4 create paths:** `items_import.py` commit (classifier fallback when no Tally Stock Group), `inward/approve.py`, `invoices/finalize.py`, `items.py` manual create (suggest when both unset). `items.py` PATCH group change on unconfirmed item → learning hook. `auth.py` register: `seed_synonyms` (+`flush()`) then `seed_taxonomy` — replaced the old 8-name `_SEED_CATEGORIES`.

**Measured on the real 2,094:** 64% keyword rule, 34% HSN fallback, 2% none → **97% in a real department, 64% auto-confirm, 3% (63) in Other.**

**Prod rollout — DONE** (`docker compose exec -T metalerp-api …` from `/opt/fleek-stack`; SETHIA = `cbb2c87e-14d0-45bc-8f86-627728ec7d8a`):
1. ✅ `alembic upgrade head` — 0010 applied on deploy.
2. ✅ `seed_taxonomy --tenant cbb2c87e...` (`+25 categories, +112 groups`).
3. ✅ `reclassify_items --tenant cbb2c87e... --out …` — reviewed.
4. ✅ `reclassify_items --tenant cbb2c87e... --apply` — ~2,000 existing items categorised, high-confidence → `confirmed`.

New items auto-classify on all 4 create paths. To re-run the backfill later (e.g.
after users teach rules by recategorising), repeat step 3–4 — same command, it
refiles more items with no code change. See `docs/DEPLOY-AND-OPS.md` runbook.

**Reading the reclassify CSV** — 16 columns: `item_id,name,hsn,uom,old_category_id,old_group_id,old_status,new_department,new_group,new_brand,new_category_id,new_group_id,new_status,confidence,source,rule_hit`. `"Flasks, Bottles & Thermoware"` has a comma, so `column -s, -t` misaligns every later column — use a spreadsheet or a real CSV parser. `new_brand` = e.g. `Milton` (correct — the brand); the real `new_category_id` is the column after it (a UUID).

**Decisions carried forward:**
- **Legacy categories left in place** — the old 8 `_SEED_CATEGORIES` ("Steel"/"Stainless"/…) still exist on tenants that registered before this slice; the classifier assigns nothing to them. No `--prune-legacy` flag was built. Delete via `DELETE /api/item-categories` (reassigns/detaches, never blocks) if wanted, after the backfill.
- **Two minor departments folded into "Other"** (Water & Filtration ~24, Metal/Trade-raw ~11) — their keyword phrases stay in `RULES` pointed at `OTHER_DEPARTMENT`; a re-split is a one-line re-point.
- **Golden CSV is the regression guard** — `test_golden_matches_current_classifier` fails on any unintended classification shift. Regenerate deliberately after a `RULES` edit (snippet in the test docstring).

**Known classifier misfires to tune later** (visible in the golden CSV, not blocking): a stray tea-strainer → Pooja; a dough *scraper* → "Scrap (folded)"; `SR-WA18H` rice cooker matched the text rule not the Panasonic brand (`normalize` strips the hyphen → `sr wa18h`, brand phrase `sr wa` misses). Fix by reordering/tightening phrases in `item_taxonomy.RULES`, then regenerate the golden CSV.

---

_Original plan below._

---

## 1. The problem

- 2,094 items imported from Tally with **zero stock groups / stock categories** — Tally did no organizing. Every item came in `group_id = NULL`, `category_id = NULL`.
- This is **~20% of the real catalogue** (~10k items, growing). So this is **not** a one-time cleanup — it's *"what assigns a category + group to an item the moment it's created, forever."*
- Items get created from **four** paths, all of which must feed one classifier:
  | Path | Today | Needs |
  |---|---|---|
  | Tally item import (`items_import.py`) | group from Tally Stock Group; category from group's 1st token | fallback when Stock Group absent |
  | Inward bill import (X0→X5) | fuzzy-match or create loose | classify on create |
  | Billing type-ahead (`LineRow` v2) | create loose | classify on create |
  | Manual add (Items UI) | user picks | suggest + confirm |

---

## 2. Decisions locked (from user)

1. **Hybrid `category`** — brand where a real brand exists (Hawkins, Prestige, Panasonic, Milton, Cello, Borosil, …), **department** otherwise.
2. **Curated groups, minimum human intervention** — classifier assigns to an *existing* group or to `Other`; new groups are added by editing the seed taxonomy, **not** auto-invented. The seed taxonomy below is deliberately wide so edits stay rare.
3. **Full build** in one push, after this doc is signed off.

---

## 3. How `category` / `group` map onto the existing schema

No schema change. Using `item_category` + `product_group` exactly as their docstrings already describe:

| Level | Table | This shop | Field notes |
|---|---|---|---|
| **Category** | `item_category` | a **brand** OR a **department** name — one flat per-tenant list, `name` + `sort` | `category_id` authoritative; legacy `category` string left as fallback |
| **Group** (`ProductGroup`) | `product_group` | **product type** — "Kadai", "Rice Cooker", "Jhula". Carries shared `hsn_code`, `uom`, `item_type`, `default_rate_mode` | `name_normalized` dedupes wording drift |
| **Item** | `item` | the leaf / size variant, `size_label` parsed from the name | inherits category+HSN+UOM from group unless it overrides |

**Hybrid rule for `category_id`:**
- If a brand is detected on the name → `category` = that brand (created if new, capped list — see §6).
- Else → `category` = the item's **department** (from the group's department).
- `group_id` is always set from the department taxonomy (or left null → "Other").

So a Hawkins cooker: `category = "Hawkins"`, `group = "Pressure Cooker"`.
A generic kadai: `category = "Cookware"`, `group = "Kadai / Kadhai"`.
Both navigable; `generated_name()` prints `"Hawkins Pressure Cooker 3L"` / `"Cookware Kadai 240MM"`.

---

## 4. Proposed taxonomy — 12 departments, ~85 groups

Counts = items from the 2,094 sample that a first-pass rule table + HSN fallback places here. Groups with 0 sample hits are kept anyway (they *will* fill from the other 8k).

### Steel Utensils & Serveware  — ~619 (29.6%)
Thali / Plate · Bowl / Katori · Glass / Tumbler · Jug · Mug · Serving Bowl / Donga · Table Spoon / Fork · Dinner Set · Balti Set (gift set) · Lota / Gadva / Golchi · Tiffin / Lunch Box · Steel Container / Ghee Pot · Kitchen Rack / Stand · Tray / Plate Stand · Ash Tray

### Kitchen Appliances  — ~278 (13.3%)
Rice Cooker · Electric Kettle · Induction Cooktop · Mixer / Grinder · OTG / Oven / Grill · Gas Stove / Hob · Toaster / Sandwich Maker · Electric Fryer / Air Fryer · Garment Iron · Atta / Dough Machine · Hand Blender / Beater · Heater / Hot Plate / Immersion Rod

### Cookware  — ~219 (10.5%)
Kadai / Kadhai · Fry Pan · Tawa · Handi · Patila / Topa / Bhagona · Sauce Pan / Milk Pan · Aluminium Saucepan w/ Cover · Cookware / Handi Set · Appam / Idli / Paniyaram · Aluminium Pot · Aluminium Utensils (loose)

### Plasticware  — ~219 (10.5%)
Plastic Storage Container · Insulated Casserole (plastic) · Plastic Bucket / Bath Set · Plastic Jug / Bottle · Plastic Tiffin · Plastic Basket / Tray · Chopping Board · Money Bank / Piggy Bank · Cooler Box (Kool)

### Cutlery & Kitchen Tools  — ~199 (9.5%)
Knife / Chopper · Skimmer / Jhara / Palta · Masher / Ghotni · Grater / Slicer / Kisni · Peeler / Cutter · Chimta / Tong / Sansi · Sarota / Nut Cracker · Tea Strainer / Chalni · Chakla Belan / Rolling Pin · Sil Batta / Mortar · Whisk / Mathani / Egg Whisk · Aata Scoop · Ice Cream Scoop · Kitchen Tool Set

### Flasks, Bottles & Thermoware  — ~127 (6.1%)
Vacuum Flask / Thermos · Steel Water Bottle · Copper Bottle / Jug · Insulated Casserole (hot-pot) · Beverage Dispenser / Airpot · Tea Can / Tea Container · Water Jug (double-wall)

### Pressure Cookers  — ~105 (5.0%)
Pressure Cooker · Pressure Pan · Cooker Gasket · Cooker Spare - Other (valve, weight, handle, lid) · Cooker (generic / unclassified size)

### Pooja & Wooden Goods  — ~104 (5.0%)
Jhula / Palna · Bajot / Chowki / Patla · Mandir / Temple · Jewellery Box · Dry Fruit Box · Bangle Box · Puja Thali / Kalash / Diya / Bell · Weight Box / Cash Box (Golak) · Wooden Spoon / Board · Puper W-Box (OX/Gold)

### Glassware & Crockery  — ~75 (3.6%)
Storage Jar · Drinking Glass Set · Cup & Saucer · Ceramic / Glass Mug · Glass Bowl · Opalware / Crockery Dinner Set · Casserole (glass)

### Household & Cleaning  — ~66 (3.2%)
Bucket - GI / Steel · Bucket - Aluminium · Tub / Ghamela / Parat · Gamla / Planter · Mop · Broom / Wiper / Brush · Dustbin · Drying Stand / Hanger · Lighter / Agarbatti Stand · Padlock / Hardware · Coal Iron (non-electric)

### Furniture  — ~8 (0.4%)
Chair · Stool · Table · Rack / Shelf / Almirah · Garden Swing · Bed / Bedding

### Other / Uncategorised  — ~75 (3.6%)
Catch-all. Anything with no keyword hit **and** no usable HSN chapter, **plus** the two folded minor lines below. Item lands here `status = unconfirmed`; recategorizing it in the UI teaches a rule (§7).

**Folded in (minor, not in operation — 2026-09-02):**
- **Water & Filtration** (~24 — Bharati water filters, filter candles, camper/surahi) → Other
- **Metal / Trade (raw)** (~11 — scrap, sheet/circle/patti, brass utensils, packing) → Other

If either line reactivates, split it back out of Other by adding its rules to `item_taxonomy.py` — the keyword phrases stay in the table, just re-pointed at a live department.

**Coverage on the 2,094 sample:** 59% by keyword rule to a live department, +38% by HSN-chapter fallback, **~4% (75 items) in Other.**

---

## 5. The classifier — `app/domain/item_classify.py`

Pure, no I/O, mirrors `product_parse.py`. Signature:

```python
def classify_item(
    name: str,
    *,
    hsn: str | None = None,
    uom: str | None = None,
    brands: list[str],                     # tenant's item_category names that are brands
    rules: list[ClassifyRule],             # seed table + tenant-learned rows
    synonyms: dict[str, str] | None = None,
) -> ClassifyResult:
    # -> (department, group_name, brand | None, confidence, rule_hit)
```

Pipeline (first hit wins):
1. **normalize** name (reuse `app.domain.normalize.normalize_name` + synonym map — bartan/Hindi terms already collapse, [[bartan-synonyms-plan]]).
2. **brand scan** — longest brand string first (same approach as `product_parse._match_brand`).
3. **keyword rules** — ordered `(department, group, [phrases])` table. ~85 groups, ~400 phrases. Specific before generic (`RICE COOKER` before ` COOKER `).
4. **HSN-chapter fallback** — 2-digit chapter → department, group = department's "generic" bucket. Confidence 0.4.
5. **no hit** → `Other / Uncategorised`, confidence 0.0.

`confidence` gates status, not a human queue:
- ≥ 0.7 → assigned, `status = confirmed`
- 0.3–0.7 → assigned to best guess, `status = unconfirmed` (Items UI already filters on this)
- < 0.3 → `Other`, `status = unconfirmed`

The seed rule table + brand list live in `app/domain/item_taxonomy.py` as plain data, version-controlled.

---

## 6. Seed & brand cap

- **`_SEED_CATEGORIES` in `auth.py`** is replaced: on register, seed the **12 departments** + the **starter brand list** (~16 known metal-trade brands) as `item_category` rows. `sort` orders departments first, brands after.
- **Existing tenants**: an idempotent `seed_departments(tenant_id)` — adds any missing department/brand rows, touches nothing else. Run once for SETHIA.
- **Brand cap**: classifier only *creates* a new brand category if the brand string appears on **≥ 3 items** in the same run (prevents a one-off "12417-S/PG" becoming a category). Below that → treated as department. Tunable.

---

## 7. Learning loop (the "minimum human intervention" part)

Reuses the alias-learning pattern already built for names ([[invoice-generation-slice]] Loop 2).

- New table **`item_classify_rule`** (per-tenant): `keyword`, `department`, `group_id`, `source` (`seed` | `learned`), `hits`, `last_used_at`.
- When a user **recategorizes an `unconfirmed` item** in the Items UI:
  - the distinctive token(s) of that item's normalized name → a `learned` rule pointing at the chosen group.
  - next import matches it automatically. Import #2 is smarter than #1 with no code change.
- `learned` rules with `last_used_at` > 120 days and `hits` < 2 are swept nightly (same job as `alias_sweep`).
- Seed rules are immutable from the UI; only editable in `item_taxonomy.py`.

---

## 8. Wiring

| File | Change |
|---|---|
| `app/domain/item_classify.py` | **new** — the classifier |
| `app/domain/item_taxonomy.py` | **new** — seed departments, brand list, keyword rule table, HSN-chapter map |
| `app/models/item_classify_rule.py` + migration `0010` | **new** — `item_classify_rule` table |
| `app/routers/auth.py` | replace `_SEED_CATEGORIES`; call `seed_departments` on register |
| `app/services/catalogue/seed_departments.py` | **new** — idempotent top-up for existing tenants |
| `app/routers/items_import.py` | when Tally Stock Group absent/uninformative → `classify_item`; set `category_id`/`group_id`/`status` from result |
| inward create path (`app/services/inward/*` or `item_resolution.py`) | classify on item create |
| billing type-ahead item create (`LineRow` v2 backend) | classify on item create |
| `app/routers/items.py` | on PATCH that changes `group_id` of an `unconfirmed` item → write a `learned` rule |
| `tools/reclassify_items.py` | **new** — CLI: run `classify_item` over all existing items of a tenant (dry-run → CSV; `--apply` writes). Used for the SETHIA 2,094 and every future bulk. Same code path, not bespoke. |
| `web/` Items UI | show department/group/confidence; "unconfirmed" badge; recategorize control already mostly there |

---

## 9. Tests

- `tests/test_item_classify.py` — the 2,094 sample as a fixture; assert ≥ 95% land outside "Other", assert a hand-labelled golden set of ~60 (one per group) classifies correctly.
- `tests/test_seed_departments.py` — idempotency, brand cap.
- `tests/test_classify_learning.py` — recategorize → rule row → next classify picks it up.
- Golden CSV of the full 2,094 committed under `tests/fixtures/` for regression.

---

## 10. Deliverables for the one push

1. `item_classify.py` + `item_taxonomy.py` (12 depts, ~85 groups, ~400 phrases, ~16 brands, HSN map)
2. migration `0010` + `item_classify_rule` model
3. `seed_departments` service + `auth.py` swap
4. classifier wired into all 4 create paths
5. `tools/reclassify_items.py` CLI
6. learning hook in `items.py` PATCH
7. tests + committed golden CSV
8. Items UI: department/group/confidence display + recategorize writes a rule
9. Run `reclassify_items --apply` for SETHIA's 2,094 as the final step; report the resulting distribution

**Not committed / not pushed** — user does check-ins ([[ui-plan-guardrails]]).

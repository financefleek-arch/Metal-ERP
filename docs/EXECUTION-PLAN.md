# Execution Plan — to First Printed Bill

**Goal:** a deployed web app where the shop can create a non-GST invoice — pick a party, add line items (typed, with type-ahead), enter qty/rate, finalize, and download a print-accurate A4 PDF matching the sample layout (minus GST columns).

**Explicitly out of scope for this milestone:** GST/IRN/e-Way Bill, weighbridge agent, barcodes/scanning, Tally push, mobile app, multi-touchpoint role split, stock tracking, payments/outstanding. All are designed-for in the schema but dormant. Tally *import* is optional and can run before or after first bill.

---

## 0. Foundations (½ week)

| Task | Detail |
|---|---|
| Repo + tooling | Monorepo (pnpm workspaces): `api/` (Fastify + TS), `web/` (React + Vite + TS), `packages/tax-core`, `packages/tally-import` (stub). ESLint, Prettier, tsconfig base. |
| CI | GitHub Actions: typecheck, lint, unit tests, build. |
| Local dev | `docker-compose`: Postgres 16, MinIO (S3), Mailpit (SMTP catch). `.env` template. |
| Deploy target | Decide now: single VPS (Docker Compose) or a PaaS. Provision Postgres + object storage + a container host. One staging env. |
| Error tracking | Sentry (or equivalent) wired into api + web from day one. |

**Exit:** `pnpm dev` runs api + web + db locally; a hello-world route deploys to staging via CI.

---

## 1. Data model + migrations (½ week)

Prisma schema with the **full** table set from the design, GST/stock/barcode columns present but nullable/defaulted:

- `tenant`, `user`
- `party`, `party_address`
- `item`, `item_alias`, `synonym`, `item_category` (enum-backed), `product_group` *(table exists, unused)*
- `hsn_code` (reference table, seeded)
- `uom` (enum or small table)
- `invoice`, `invoice_line`
- `number_sequence`
- `audit_log`

Decisions baked in now:
- Money: `NUMERIC(15,2)` columns; all internal calc in integer paise.
- `invoice.status`: `DRAFT | FINAL | CANCELLED`. Totals columns frozen on finalize.
- `number_sequence` keyed `(tenant_id, series, fy)`, row-locked, gap-free. FY = Apr–Mar.
- RLS on `tenant_id` for every tenant-scoped table; app sets `SET app.tenant_id` per request. *(Single tenant now, but the guard is cheap and avoids a retrofit.)*
- `template_version` on `invoice`, default `'v1-nongst'`.

**Exit:** `prisma migrate` runs clean on staging; seed script inserts one tenant, one admin user, the HSN reference list, the UOM + category enums, and the ~30-entry synonym seed.

---

## 2. `packages/tax-core` (½ week, parallelisable with §1)

Pure, no I/O. One function:

```ts
computeInvoice(input): ComputedInvoice
// subtotal, line discounts, invoice discount, round-off, amount-in-words
```

- Round-off: nearest rupee (config flag, default on).
- Amount-in-words: Indian system (lakh/crore), "…and NN paise Only".
- **Unit tests are the deliverable**: lock round-off and words against a table of known inputs, including the sample invoice's numbers (₹2,26,287.00 line, round-off −0.36 case adapted, words output).

Compiled to both Node (api) and browser (web live preview) — same code path, so preview and PDF never disagree.

**Exit:** `pnpm test` green; coverage on the words + rounding branches.

---

## 3. Auth + tenant + shell (½ week)

- Email + password login (Argon2 hashes). Session cookie or JWT. TOTP optional — skip for first bill, leave the column.
- Roles `owner | accountant | viewer` in the model; enforce `owner`/`accountant` can write, `viewer` read. No per-touchpoint roles yet.
- React app shell: top nav (Dashboard / Sales / Parties / Items), auth guard, API client (TanStack Query), form stack (RHF + Zod), Tailwind + the chosen visual tokens.
- **Tenant onboarding screen** (the Onboarding artboard): business name, address, city, state, PIN, phone, PAN, document label, bank block. Writes `tenant`.

**Exit:** admin logs in on staging, fills company profile, sees an empty dashboard.

---

## 4. Party + Item CRUD + normalization (1 week)

**Party**
- List + create/edit. Fields: name, phone, email, PAN, one address (line1–3, city, state, pincode). `role` defaults `customer`; GSTIN field present, hidden.

**Item**
- `name` + computed `name_normalized` (normalization pipeline: lowercase → trim/collapse → strip punctuation → synonym map → keep token order).
- `uom` (enum picker), `category` (enum picker), `item_type` (BULK/MRP toggle, default BULK), `hsn_code` (searchable lookup against `hsn_code`, nullable), `default_rate`/`last_rate`.
- `source` (`MANUAL | AUTO_FROM_INVOICE | IMPORT`), `status` (`UNCONFIRMED | CONFIRMED`).
- Trigram index (`pg_trgm`) on `name_normalized` for fuzzy search.
- **No merge tool / variant grouping / label printing yet** — Stage 1+ work. Just CRUD + the normalized-key dedupe on create.

**Exit:** can add a party and a few items by hand; searching items is fuzzy; duplicate normalized names are rejected on create.

---

## 5. Invoice editor + live preview (1.5 weeks) — the core

- **Header:** pick party → address auto-fills (editable); invoice date; series = `Sales`; number shown as "auto on finalize".
- **Line grid:**
  - Item field = **type-ahead combobox**: searches existing items (fuzzy, confirmed first); Enter picks the highlighted match; arrow to "+ Create new '<text>'" to diverge. Near-miss soft nudge.
  - Per line: description (frozen from item or free text), `hsn_sac`, `qty`, `uom`, `unit_rate`, line discount → `line_total`.
  - Free-typed name with no match is allowed — item row is created on **finalize**, not mid-type.
- **Totals panel:** subtotal, discount, round-off, grand total, amount-in-words — computed client-side via `tax-core`.
- **Live A4 preview:** the same HTML template the PDF uses, rendered in the editor from the draft.
- **Save draft:** persists `invoice` (status DRAFT, no number) + `invoice_line`s.
- Concurrency: simple — last-write-wins on the whole draft, single editor assumed. No real-time sync yet.

**Exit:** build a multi-line draft, see correct live totals and a correct on-screen preview, reload and it persists.

---

## 6. Finalize + numbering + item accretion (1 week)

Single transaction on **Finalize**:
1. Validate: every line has qty > 0 and rate > 0; party set. Block otherwise.
2. Assign number from `number_sequence` (row lock, `(tenant, 'Sales', fy)`), gap-free.
3. Freeze: write `subtotal`, `discount_total`, `round_off`, `grand_total`, `amount_in_words`, `template_version`, snapshot `terms`/`declaration` text.
4. For each line: normalize description → find item by `name_normalized` / alias → if none, **create** `item` (`source=AUTO_FROM_INVOICE`, `status=UNCONFIRMED`, uom from line, `last_rate`); else bump `last_rate`, `last_sold_at`, `times_billed`. Link `invoice_line.item_id`.
5. Set `status = FINAL`. Write `audit_log`.
6. Enqueue `render-pdf` job.

**Exit:** finalizing a draft assigns #1, freezes totals, and the typed items now exist in the catalogue as unconfirmed.

---

## 7. PDF rendering (1.5 weeks)

- **Template** `invoice-v1-nongst.html` — boxed-grid layout mirroring the sample: seller block, consignee + buyer blocks, reference grid, line table (**no GST columns**), totals ladder, amount-in-words row, bank block, declaration, jurisdiction line, "This is a Computer Generated Invoice". Title = tenant's document label ("Invoice" / "Bill of Supply").
- `@page A4`, mm units, fixed table layout, print CSS. Two-page capable but v1 is one page.
- **Renderer:** Puppeteer + headless Chromium (single instance now; pool later). Job consumes the finalized invoice → renders → uploads PDF to object storage → sets `invoice.pdf_url`.
- Same template file feeds §5's live preview (via SSR or a shared render function).
- Optional "Scan to pay" UPI QR from `tenant.upi_id` — include if trivial, else defer.

**Exit:** finalize → within seconds a **Download PDF** button appears → the PDF opens and visually matches the sample (minus GST bits) on A4.

---

## 8. Invoice list + wrap-up (½ week)

- Invoice list: number, date, party, amount, status; filter by status/date; open → view + Download PDF.
- Actions: **Duplicate** (new draft from an existing invoice), **Cancel** (status CANCELLED, number not reused).
- Dashboard: sales-this-month total, invoice count, recent invoices, count of unconfirmed items. (The "items to review" panel can be a stub link.)
- Basic **invoice register** (list export to CSV).

**Exit:** the shop can find, reopen, re-print, duplicate and cancel invoices.

---

## 9. Optional pre-seed — Tally XML import (1 week, can run in parallel from §4 onward)

Only if the shop wants their existing catalogue loaded before bill #1.

- **Source:** one-time `File → Export` from Tally Prime to XML (Stock Items, Ledgers, Units, Stock Groups). No live connection needed for the initial seed.
- `packages/tally-import`: parse Tally XML exports (UTF-16/BOM aware; strip control-char entities) → `staging_tally_item` / `staging_tally_ledger` (keep raw blob + GUID/MASTERID).
- Map → `item` / `party`; run the §4 normalization; auto-confirm rows with uom + hsn + group, queue the rest as unconfirmed.
- A minimal **import review screen**: counts, the unconfirmed/dupe list, accept.

**Exit:** ~200 items + parties visible in the pickers, mostly a quick confirm. Not on the critical path to first bill.

---

## 10. Hardening + go-live (1 week)

- Seed production tenant + owner login; load HSN list, enums, synonyms.
- Backups: nightly Postgres dump + object-storage lifecycle. Test a restore.
- Smoke test on production: onboard → add party → add item → draft → finalize → PDF, end to end.
- Basic runbook: how to restart, where logs/Sentry are, how to reset a number sequence, how to re-render a PDF.
- Walk the shop through creating **bill #1**. Watch them do it; fix the top 3 friction points.

**Exit:** first real bill printed by the shop.

---

## Timeline

| Phase | Duration | Notes |
|---|---|---|
| 0 Foundations | 0.5 wk | |
| 1 Data model | 0.5 wk | ‖ with 2 |
| 2 tax-core | 0.5 wk | ‖ with 1 |
| 3 Auth + shell | 0.5 wk | |
| 4 Party/Item CRUD + normalization | 1 wk | |
| 5 Invoice editor + preview | 1.5 wk | critical path |
| 6 Finalize + numbering + accretion | 1 wk | |
| 7 PDF rendering | 1.5 wk | critical path; can start template in wk 3 |
| 8 Invoice list + wrap-up | 0.5 wk | |
| 10 Hardening + go-live | 1 wk | |
| **Total (critical path)** | **~8–9 weeks** | one full-stack dev |
| 9 Tally import (optional) | +1 wk | parallel, off critical path |

**Two devs:** one owns api/data/tax-core/finalize (§1,2,6), the other owns web/editor/preview/PDF (§3,5,7) → **~5–6 weeks**.

---

## Critical path & risks

1. **§5 Invoice editor** and **§7 PDF fidelity** are the long poles. Start the PDF template as static HTML in week 2–3, in parallel, so §7 is mostly wiring by the time it's reached.
2. **PDF pixel-matching** the dense boxed table is the classic time sink — timebox it, get "clearly correct and professional" not "byte-identical to Tally's output".
3. **Puppeteer in production** (Chromium deps, memory) — validate on the real deploy target early, not at §10.
4. **Amount-in-words** edge cases (paise, exact lakhs, zero) — covered by §2 tests, don't hand-roll later.
5. Keep dormant-feature columns **nullable with sane defaults** so none of Phase 2 needs an `ALTER` on a populated `invoice` table.

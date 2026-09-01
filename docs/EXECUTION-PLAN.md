# Execution Plan — to First Printed Bill

**Goal:** a deployed web app where the shop can create a non-GST invoice — pick a party, add line items (typed, with type-ahead), enter qty/rate, finalize, and download a print-accurate A4 PDF matching the sample layout (minus GST columns).

**Explicitly out of scope for this milestone:** GST/IRN/e-Way Bill, weighbridge agent, barcodes/scanning, Tally push, mobile app, multi-touchpoint role split, **inventory / stock tracking**, payments/outstanding. All are designed-for in the schema but dormant. Tally *import* is optional and can run before or after first bill.

> **Inventory is Stage 3, not M1.** M1 has no stock quantities, no opening-stock entry, no in/out movements, no low-stock views, and no purchase-bill screen. Selling an item the catalogue doesn't know about just auto-creates it. See `DESIGN.md` → *Maturity ladder*.

## Screens shipping in M1

| Screen | Phase | Status |
|---|---|---|
| Register / Login | 3 | ✅ done |
| App shell + top nav + auth guard | 3 | ✅ done |
| Firm profile (onboarding: address, bank block, document label, PAN/state validation) | 3 | ✅ done |
| Parties — list + search + role filter, two-pane inline editor (address, PAN/GSTIN/state validation), **+ Tally masters import** | 4 | ✅ done |
| Items — two-pane list + detail, filter chips, structured metal-trade attributes, price band, HSN→GST, confirm/merge | 4 | ✅ done |
| Item catalogue **hierarchy** — category → product-group → item, per-tenant categories, tree view, `product_parse` + `resolve_group` (migration `0007`) | 4+ | ✅ done |
| **Tally item import** — Masters stock-items XML → GUID→name→create ladder → review → commit, builds product-groups (migration `0008`) | 9 | ✅ done |
| **Invoice editor** — party picker, item type-ahead combobox, line grid, live totals panel, save draft | **5** | ⬜ |
| **Printed invoice** — A4 (WeasyPrint) + an in-editor React preview that mirrors the same markup | **7** | ⬜ |
| **Invoice list** — filters, open → view/download PDF, duplicate, cancel | **8** | ⬜ |
| **Dashboard** — sales-this-month tile, recent invoices, unconfirmed-items count | **8** | ⬜ |

Synonym-editor UI is deferred to Stage 1 — M1 ships the seeded ~35-entry list, no editor.

> **Note (2026-09-01):** the item master grew well past the original "ItemDrawer" sketch below.
> It's now a two-pane editor + a `category → product-group → item` hierarchy with a tree view,
> a rules-first shorthand parser (`app/domain/product_parse.py`), and both a Tally party importer
> and a Tally item importer. The self-learning-catalogue arc (inward bills seed items, billing
> refines them) is designed in `docs/visual-plan/catalogue-learning-review.html` — phase 1
> (hierarchy) + the Tally item importer are **done**; the two learning loops wait on the inward
> module and the invoice editor. The rows below describe the original minimal plan and are kept
> for history.

---

## 0. Foundations (½ week)

| Task | Detail |
|---|---|
| Repo + tooling | `api/` (FastAPI + SQLAlchemy 2.0 + Alembic, `pyproject.toml`, ruff + mypy), `web/` (React + Vite + TS, ESLint/Prettier), `tools/tally_import/` (stub). |
| CI | GitHub Actions: ruff + mypy + pytest for api; typecheck + lint + build for web. |
| Local dev | `docker-compose`: Postgres 16 (with `pg_trgm`), Mailpit (SMTP catch). `.env` template — **never committed**; hand the dev the lines. |
| Deploy target | Single VPS, Docker Compose, sharing the existing Postgres instance via a dedicated `metalerp` database + role. One staging schema (`DB_SCHEMA=staging`), same isolated-schema discipline as fleek-backend. |
| Error tracking | Reuse the self-hosted error tracker fleek uses; wire api + web from day one. |
| PDF | No object storage for M1 — WeasyPrint writes PDFs to a bind-mounted volume; `invoice.pdf_path` holds the relative path. Swap to S3-compatible storage later without a code change to callers. |

**Exit:** `uvicorn` runs the api and `pnpm dev` the web locally against the docker Postgres; a `/health` route deploys to the staging schema via CI.

**Key dependencies:** `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `alembic`, `psycopg[binary]`, `pydantic-settings`, `passlib[argon2]`, `python-jose` (JWT), `weasyprint`, `jinja2`, `segno` (QR), `lxml` (Tally import), `pytest`, `ruff`, `mypy`.

---

## 1. Data model + migrations (½ week)

SQLAlchemy 2.0 models (typed `mapped_column`) with the **full** table set from the design, GST/stock/barcode columns present but nullable/defaulted. Alembic autogenerate → reviewed migration → `alembic upgrade head`.

- `tenant`, `user`
- `party`, `party_address` *(+ `staging_tally_party`, `0005`)*
- `item`, `item_alias`, `synonym`, `item_category` *(per-tenant table, `0007` — was planned enum-backed)*, `product_group` *(surfaced `0007`; `category_id` / `default_rate_mode` / `name_normalized` added)*
- `staging_tally_item` *(`0008`)*
- `hsn_code` (reference table, seeded)
- `uom` (enum or small table)
- `invoice`, `invoice_line`
- `number_sequence`
- `audit_log`

Decisions baked in now:
- Money: `NUMERIC(15,2)` columns; all internal calc in integer paise (`int`), formatted at the edge.
- `invoice.status`: `DRAFT | FINAL | CANCELLED` (Python `enum` + PG enum). Totals columns frozen on finalize.
- `number_sequence` keyed `(tenant_id, series, fy)`, claimed with `SELECT … FOR UPDATE` in the finalize transaction, gap-free. FY = Apr–Mar.
- Tenant scoping enforced in a SQLAlchemy session-level filter / a `tenant_id` arg on every query helper. *(Single tenant now; Postgres RLS optional — the app-level guard is enough for M1 and matches fleek's pattern.)*
- `template_version` on `invoice`, default `'v1-nongst'`.

**Exit:** `alembic upgrade head` runs clean on the staging schema; `seed.py` inserts one tenant, one admin user, the HSN reference list, the UOM + category enums, and the ~30-entry synonym seed.

---

## 2. `app/domain/tax.py` (½ week, parallelisable with §1)

Pure Python, no I/O:

```python
def compute_invoice(inp: InvoiceInput) -> ComputedInvoice:
    # subtotal, line discounts, invoice discount, round-off, amount-in-words
```

- Round-off: nearest rupee (config flag, default on).
- Amount-in-words: Indian system (lakh/crore), "…and NN paise Only".
- **pytest is the deliverable**: lock round-off and words against a table of known inputs, including the sample invoice's numbers (₹2,26,287.00 line, round-off −0.36 case adapted, words output). Export that table as `tests/vectors/tax_vectors.json`.
- The web live-preview total is a **small duplicated JS helper** (`web/src/lib/previewTotal.ts`) covering only subtotal + round-off + words; a Vitest test runs it against the same `tax_vectors.json` so the two implementations can't drift. The authoritative number on finalize always comes from the Python side.

**Exit:** `pytest tests/domain` green; the shared vector file passes on both sides.

---

## 3. Auth + tenant + shell (½ week)

- Email + password login (`passlib` / Argon2 hashes). JWT access token (mirror fleek-backend's auth approach). TOTP optional — skip for first bill, leave the column.
- Roles `owner | accountant | viewer` in the model; a FastAPI dependency enforces `owner`/`accountant` can write, `viewer` read. No per-touchpoint roles yet.
- React app shell: top nav (Dashboard / Sales / Parties / Items), auth guard, API client (TanStack Query), form stack (RHF + Zod), Tailwind + the chosen visual tokens.
- **Tenant onboarding screen** (the Onboarding artboard): business name, address, city, state, PIN, phone, PAN, document label, bank block. `POST /tenant` writes it.

**Exit:** admin logs in on staging, fills company profile, sees an empty dashboard.

---

## 4. Party + Item CRUD + normalization (1 week)

### Party  ✅ done

- API: `GET /api/parties` (name search `q`, `role` filter), `POST`, `GET/{id}`, `PATCH/{id}`, `DELETE/{id}` — all tenant-scoped, dup-name 409.
- Schema-level PAN / GSTIN / GST-state-code validation (`app/reference.py`), `GET /api/reference/states`.
- **UI:** Parties page (table, search box, role filter chips) + a slide-in **PartyDrawer** (name, role, phone, email, PAN, GSTIN, default state, one address). State fields are a `StateSelect` dropdown; PAN/GSTIN inputs uppercase + format-hint.

### Item  ⬜

**API**
- `GET /api/items` — `q` (fuzzy over `name_normalized` + aliases via `pg_trgm`), `type`, `status`, `no_hsn` filters; confirmed-first ordering.
- `POST /api/items`, `GET/PATCH/DELETE /api/items/{id}` — tenant-scoped, normalized-key dedupe (409 on a colliding `name_normalized`).
- `GET /api/reference/hsn?q=` — searchable HSN lookup (code or description).
- `GET /api/reference/uoms`, `/categories` — enum lists for the pickers.
- `app/domain/normalize.py` — the pipeline: lowercase → trim/collapse whitespace → strip punctuation → apply the tenant's `synonym` map → keep token order. Used by item create/patch and (later) by finalize accretion. Unit-tested against a table of messy inputs.

**UI**
- **Items page** — table: name, type badge (⚖ BULK / 📦 MRP), category, HSN, UOM, last rate, status. Search box (fuzzy) + filter chips: All / BULK / MRP / Unconfirmed / No HSN. Row → open the drawer.
- **ItemDrawer** (add/edit) — name; BULK/MRP toggle (default BULK); category select; UOM select; **HSN searchable lookup** (type code or words → pick from `hsn_code`); default rate; MRP + default discount % (shown only for MRP). On save: normalized-key collision → inline "looks like an existing item: <name>".
- No merge tool / variant grouping / label printing — Stage 1+.

**Exit:** add a party and items by hand; item search is fuzzy and synonym-aware; a colliding normalized name is rejected on create with a pointer to the existing item.

---

## 5. Invoice editor + live preview (1.5 weeks) — the core

**API**
- `POST /api/invoices` → new DRAFT (no number); `GET /api/invoices/{id}`; `PUT /api/invoices/{id}` replaces header + all lines (last-write-wins, single-editor assumed); `GET /api/invoices` (list, Phase 8).

**UI — `InvoiceEditorPage`**
- **Header block:** party picker (async search of `/api/parties`) → bill-to / ship-to addresses auto-fill, editable; invoice date; series = `Sales` (fixed for M1); number shows "auto on finalize".
- **Line grid** (`InvoiceLineRow` × N):
  - Item cell = **`ItemCombobox`**: debounced fuzzy search of `/api/items`; results show name + type badge + last-rate/times-billed; keyboard nav; Enter picks the top match (fills description/HSN/UOM/rate); a "+ Create new '<text>'" row to diverge; a soft near-miss banner ("close to *SS Utensil* — press Enter to use it").
  - Cells: description (from item or free text), HSN, qty, UOM, unit rate, line discount → computed line total.
  - Free-typed name with no match is allowed and stays as text — the `item` row is created at **finalize**, not mid-type.
  - Add-line / remove-line; rows renumber.
- **Totals panel** (right rail): subtotal, discount, round-off, grand total, **amount-in-words** — recompute on every keystroke via `web/src/lib/previewTotal.ts` (the small JS mirror of `domain/tax.py`, kept in lockstep by the shared `tax_vectors.json`). The finalize response returns the authoritative Python numbers.
- **Live A4 preview** (`InvoicePreview` component): a React render of the *same* markup + CSS the WeasyPrint template uses, fed from the draft — so the editor and the printed PDF stay visually aligned.
- **Save draft** button → `PUT /api/invoices/{id}`; a "Finalize" button, disabled until every line has qty > 0 and rate > 0 and a party is set, with the blocking reasons listed.

**Exit:** build a multi-line draft, see correct live totals + amount-in-words + on-screen A4 preview, reload and it persists; the Finalize button gates correctly.

---

## 6. Finalize + numbering + item accretion (1 week)

`POST /invoices/{id}/finalize` — one SQLAlchemy transaction:
1. Validate: every line has qty > 0 and rate > 0; party set. Block otherwise (422).
2. Claim number from `number_sequence` (`SELECT … FOR UPDATE` on `(tenant, 'Sales', fy)`), gap-free.
3. `compute_invoice(...)` → freeze `subtotal`, `discount_total`, `round_off`, `grand_total`, `amount_in_words`, `template_version`, snapshot `terms`/`declaration` text.
4. For each line: normalize description → find item by `name_normalized` / alias → if none, **create** `item` (`source=AUTO_FROM_INVOICE`, `status=UNCONFIRMED`, uom from line, `last_rate`); else bump `last_rate`, `last_sold_at`, `times_billed`. Link `invoice_line.item_id`.
5. Set `status = FINAL`. Write `audit_log`.
6. Render the PDF **synchronously** (WeasyPrint, ~0.3–1 s — no queue needed for M1) → write to the PDF volume → set `invoice.pdf_path`. If the render raises, the finalize still commits; a `pdf_status` flag marks it for a manual re-render endpoint.

**UI:** the editor's "Finalize" button calls this; on success it flips the page to a **read-only finalized view** with the assigned number, frozen totals, and a **Download PDF** button (+ a "Re-render" affordance if `pdf_status = failed`).

**Exit:** finalizing a draft assigns #1, freezes totals, produces the PDF, and the typed items now exist in the catalogue as unconfirmed.

---

## 7. PDF rendering (1.5 weeks)

- **Template** `app/templates/invoice_v1_nongst.html` + `invoice_v1.css` — boxed-grid layout mirroring the sample: seller block, consignee + buyer blocks, reference grid, line table (**no GST columns**), totals ladder, amount-in-words row, bank block, declaration, jurisdiction line, "This is a Computer Generated Invoice". Title = tenant's document label ("Invoice" / "Bill of Supply").
- Rendered with Jinja2 → **WeasyPrint** → PDF bytes. `@page { size: A4 }`, `mm` units, fixed table layout, print CSS. WeasyPrint paginates automatically if content overflows; v1 targets one page.
- `render_invoice_pdf(invoice_id) -> Path` — pure function, called from finalize (§6) and from a `POST /invoices/{id}/rerender` endpoint. No headless browser, no system Chromium deps — just the WeasyPrint wheels (`libpango`, `libgdk-pixbuf` at the OS level, both small).
- The React live preview (§5) mirrors the same HTML structure and imports the same `invoice_v1.css`, so what the editor shows and what WeasyPrint prints stay visually aligned.
- Optional "Scan to pay" UPI QR from `tenant.upi_id` — `segno` generates it as an inline data-URI PNG; include if trivial, else defer.
- **Fonts:** ship a bundled font (e.g. a DejaVu / Noto face with the ₹ glyph) with the app so the PDF renders identically on any host.

**Exit:** finalize → a **Download PDF** button is immediately live → the PDF opens and visually matches the sample (minus GST bits) on A4.

---

## 8. Invoice list + dashboard + wrap-up (½ week)

**API:** `GET /api/invoices` (filters: status, date range, party), `POST /api/invoices/{id}/duplicate`, `POST /api/invoices/{id}/cancel`, `GET /api/invoices/{id}/pdf` (streams the file), `GET /api/dashboard/summary`, `GET /api/reports/invoice-register.csv`.

**UI**
- **`InvoicesPage`** — table: number, date, party, amount, status badge. Filter bar (status / date / party). Row → finalized view; DRAFT rows → back into the editor. Per-row actions: **Download PDF**, **Duplicate** (→ new draft, opens editor), **Cancel** (confirm → status CANCELLED, number *not* reused).
- **`DashboardPage`** — tiles: sales this month, invoices raised, new/unconfirmed items; a "recent invoices" list; an "items to review" count linking to the Items page filtered to `Unconfirmed`.
- **CSV export** button on the invoices page (invoice register).

**Exit:** the shop can find, reopen, re-print, duplicate and cancel invoices; the dashboard shows the month at a glance.

---

## 9. Optional pre-seed — Tally XML import (1 week, can run in parallel from §4 onward)

Only if the shop wants their existing catalogue loaded before bill #1.

- **Source:** one-time `File → Export` from Tally Prime to XML (Stock Items, Ledgers, Units, Stock Groups). No live connection needed for the initial seed.
- `tools/tally_import/`: parse Tally XML with `lxml` (UTF-16/BOM aware; strip control-char entities) → `staging_tally_item` / `staging_tally_ledger` (keep raw blob + GUID/MASTERID).
- Map → `item` / `party`; run the §4 normalization; auto-confirm rows with uom + hsn + group, queue the rest as unconfirmed.
- Runnable as a CLI (`python -m tools.tally_import path/to/export.xml`) plus a minimal **import review screen**: counts, the unconfirmed/dupe list, accept.

**Exit:** ~200 items + parties visible in the pickers, mostly a quick confirm. Not on the critical path to first bill.

---

## 10. Hardening + go-live (1 week)

- `alembic upgrade head` on the production `metalerp` database; run `seed.py` for HSN list, enums, synonyms, the production tenant + owner login.
- Backups: add a nightly logical `pg_dump metalerp` to its own S3/B2 path (the shared instance's physical backup covers it too, but a per-DB logical dump makes Metal ERP independently restorable). `tar` the PDF volume alongside. Test a restore into a scratch schema.
- Smoke test on production: onboard → add party → add item → draft → finalize → PDF, end to end.
- Basic runbook: how to restart the container, where logs / the error tracker are, how to reset a number sequence, how to hit `/invoices/{id}/rerender`.
- Walk the shop through creating **bill #1**. Watch them do it; fix the top 3 friction points.

**Exit:** first real bill printed by the shop.

---

## Timeline

| Phase | Duration | Status | Notes |
|---|---|---|---|
| 0 Foundations | 0.5 wk | ✅ done | repo, CI, Docker, deploy pipeline, Vault, shared PG DB |
| 1 Data model (SQLAlchemy + Alembic) | 0.5 wk | ✅ done | migrations `0001`→`0008`, single head, `seed.py` |
| 2 `domain/tax.py` + vectors | 0.5 wk | ⬜ | pure math + amount-in-words + `previewTotal.ts` |
| 3 Auth + shell | 0.5 wk | ✅ done | register/login/JWT, React shell (mobile-first), Firm profile |
| 4 Party CRUD + validation | — | ✅ done | parties + validation + StateSelect + **Tally party import** |
| 4 Item CRUD + normalization | 1 wk | ✅ done | Items two-pane + `domain/normalize.py` + HSN lookup + confirm/merge |
| 4+ Item catalogue hierarchy | — | ✅ done | category→group→item, tree view, `product_parse` + `resolve_group` (`0007`) |
| 9 Tally item import | — | ✅ done | Masters stock-items XML → review → commit, builds product-groups (`0008`) |
| 5 Invoice editor + preview | 1.5 wk | ⬜ | **critical path** — ItemCombobox, line grid, live totals, A4 preview |
| 6 Finalize + numbering + accretion | 1 wk | ⬜ | finalize txn, gap-free number, item auto-create, finalized view |
| 7 PDF rendering (WeasyPrint) | 1.5 wk | ⬜ | critical path; can start the template in parallel |
| 8 Invoice list + dashboard | 0.5 wk | ⬜ | InvoicesPage, DashboardPage, duplicate/cancel, CSV |
| 10 Hardening + go-live | 0.5 wk | ⬜ | prod seed, per-DB backup, runbook, bill #1 walkthrough |
| **Remaining critical path** | **~5 wk** | | invoice editor → finalize → PDF → list/dashboard → go-live |

**Also done, off the original plan:** the mobile-first web migration, and the design for the
self-learning catalogue (`docs/visual-plan/catalogue-learning-review.html`) — its Loop 1
(`learn_from_inward`) and Loop 2 (billing type-ahead resolve+learn) wait on the inward module
and the invoice editor respectively.

**Two devs from here:** one owns tax/normalize/items-API/finalize (§2, §4-item, §6), the other owns items-UI/editor/preview/PDF template (§4-item UI, §5, §7) → **~4 weeks** remaining.

---

## Critical path & risks

1. **§5 Invoice editor** and **§7 PDF fidelity** are the long poles. Start the PDF template as static HTML/CSS in week 2–3, in parallel, so §7 is mostly Jinja wiring by the time it's reached.
2. **PDF pixel-matching** the dense boxed table is the classic time sink — timebox it, get "clearly correct and professional" not "byte-identical to Tally's output". WeasyPrint's table/border model differs slightly from a browser's — validate the exact layout early.
3. **WeasyPrint OS deps** (`libpango`, `libgdk-pixbuf`) — install them in the api image and confirm a render works on the real deploy target early, not at §10. Far lighter than Chromium, but still needs a check.
4. **Amount-in-words** edge cases (paise, exact lakhs, zero) — covered by §2 pytest vectors, don't hand-roll later. Keep the JS preview helper in lockstep via the shared vector file.
5. Keep dormant-feature columns **nullable with sane defaults** so none of Phase 2 needs a data-migrating `ALTER` on a populated `invoice` table.
6. **Shared Postgres instance** — use a dedicated `metalerp` database + role, `statement_timeout` set, and never run migrations against the wrong database. Follow fleek-backend's isolated-schema testing rule (`DB_SCHEMA=...` + `alembic upgrade head`).

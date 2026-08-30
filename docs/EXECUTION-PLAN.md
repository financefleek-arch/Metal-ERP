# Execution Plan — to First Printed Bill

**Goal:** a deployed web app where the shop can create a non-GST invoice — pick a party, add line items (typed, with type-ahead), enter qty/rate, finalize, and download a print-accurate A4 PDF matching the sample layout (minus GST columns).

**Explicitly out of scope for this milestone:** GST/IRN/e-Way Bill, weighbridge agent, barcodes/scanning, Tally push, mobile app, multi-touchpoint role split, stock tracking, payments/outstanding. All are designed-for in the schema but dormant. Tally *import* is optional and can run before or after first bill.

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
- `party`, `party_address`
- `item`, `item_alias`, `synonym`, `item_category` (enum-backed), `product_group` *(table exists, unused)*
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
- **Totals panel:** subtotal, discount, round-off, grand total, amount-in-words — computed client-side via `previewTotal.ts` for instant feedback; the finalize response returns the authoritative Python-computed numbers.
- **Live A4 preview:** the same HTML/CSS the WeasyPrint template uses, rendered in the editor from the draft (React component mirroring the template markup).
- **Save draft:** `PUT /invoices/{id}` persists `invoice` (status DRAFT, no number) + `invoice_line`s.
- Concurrency: simple — last-write-wins on the whole draft, single editor assumed. No real-time sync yet.

**Exit:** build a multi-line draft, see correct live totals and a correct on-screen preview, reload and it persists.

---

## 6. Finalize + numbering + item accretion (1 week)

`POST /invoices/{id}/finalize` — one SQLAlchemy transaction:
1. Validate: every line has qty > 0 and rate > 0; party set. Block otherwise (422).
2. Claim number from `number_sequence` (`SELECT … FOR UPDATE` on `(tenant, 'Sales', fy)`), gap-free.
3. `compute_invoice(...)` → freeze `subtotal`, `discount_total`, `round_off`, `grand_total`, `amount_in_words`, `template_version`, snapshot `terms`/`declaration` text.
4. For each line: normalize description → find item by `name_normalized` / alias → if none, **create** `item` (`source=AUTO_FROM_INVOICE`, `status=UNCONFIRMED`, uom from line, `last_rate`); else bump `last_rate`, `last_sold_at`, `times_billed`. Link `invoice_line.item_id`.
5. Set `status = FINAL`. Write `audit_log`.
6. Render the PDF **synchronously** (WeasyPrint, ~0.3–1 s — no queue needed for M1) → write to the PDF volume → set `invoice.pdf_path`. If the render raises, the finalize still commits; a `pdf_status` flag marks it for a manual re-render endpoint.

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

| Phase | Duration | Notes |
|---|---|---|
| 0 Foundations | 0.5 wk | |
| 1 Data model (SQLAlchemy + Alembic) | 0.5 wk | ‖ with 2 |
| 2 `domain/tax.py` + vectors | 0.5 wk | ‖ with 1 |
| 3 Auth + shell | 0.5 wk | |
| 4 Party/Item CRUD + normalization | 1 wk | |
| 5 Invoice editor + preview | 1.5 wk | critical path |
| 6 Finalize + numbering + accretion | 1 wk | |
| 7 PDF rendering (WeasyPrint) | 1.5 wk | critical path; can start template in wk 3 |
| 8 Invoice list + wrap-up | 0.5 wk | |
| 10 Hardening + go-live | 1 wk | |
| **Total (critical path)** | **~8–9 weeks** | one full-stack dev |
| 9 Tally import (optional) | +1 wk | parallel, off critical path |

**Two devs:** one owns api/models/tax/finalize (§1,2,6), the other owns web/editor/preview/PDF template (§3,5,7) → **~5–6 weeks**.

---

## Critical path & risks

1. **§5 Invoice editor** and **§7 PDF fidelity** are the long poles. Start the PDF template as static HTML/CSS in week 2–3, in parallel, so §7 is mostly Jinja wiring by the time it's reached.
2. **PDF pixel-matching** the dense boxed table is the classic time sink — timebox it, get "clearly correct and professional" not "byte-identical to Tally's output". WeasyPrint's table/border model differs slightly from a browser's — validate the exact layout early.
3. **WeasyPrint OS deps** (`libpango`, `libgdk-pixbuf`) — install them in the api image and confirm a render works on the real deploy target early, not at §10. Far lighter than Chromium, but still needs a check.
4. **Amount-in-words** edge cases (paise, exact lakhs, zero) — covered by §2 pytest vectors, don't hand-roll later. Keep the JS preview helper in lockstep via the shared vector file.
5. Keep dormant-feature columns **nullable with sane defaults** so none of Phase 2 needs a data-migrating `ALTER` on a populated `invoice` table.
6. **Shared Postgres instance** — use a dedicated `metalerp` database + role, `statement_timeout` set, and never run migrations against the wrong database. Follow fleek-backend's isolated-schema testing rule (`DB_SCHEMA=...` + `alembic upgrade head`).

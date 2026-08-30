# Design

Full architecture and data model for Metal ERP. This is the target end-state; [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md) covers only what ships for the first printed bill.

## Reference document

The system must produce an invoice matching the structure of the shop's current GST tax invoice (Tally-generated): boxed grid, seller / consignee / buyer blocks, reference grid, line table (Sl / description / HSN / qty+UOM / rate / amount), totals ladder with round-off, amount-in-words, HSN tax-summary, bank block, declaration, jurisdiction, "Computer Generated Invoice", plus a second-page e-Way Bill.

**Phase 1 (non-GST)** renders the same layout minus: the "Tax Invoice" title, the IRN / Ack No. / signed-QR header, the CGST/SGST columns, the HSN tax-summary table, and the e-Way Bill page.

## Two item natures

| | BULK / transformed | MRP / trader goods |
|---|---|---|
| Examples | Steel, aluminium, iron, brass — bought as coil/sheet/ingot/scrap, cut and re-itemised | Branded utensils, hardware, fittings — bought and sold as the same SKU |
| Purchase↔sale name | Different (no 1:1 mapping) | Same (normalises to one key → auto-linked) |
| Pricing | Rate per kg, volatile, per-invoice | Has an MRP; sold at MRP or MRP − discount |
| Stock unit | Weight (Kgs / Ton) | Pieces (Nos / Pcs / Set / Dozen) |
| Barcode | Optional per-lot label carrying a weight (Stage 4) | SKU label, or maker EAN-13 |

`item.item_type` (`BULK` | `MRP`) is the discriminator; auto-guessed on creation, correctable.

## Stacks of sizes

A stack of 5 utensil sizes = **5 items**, not one. Modelled as a `product_group` with 5 `item` variants, each carrying `size_pos` (1..N, stable) and `size_label` ("smallest" … "largest").

Loose finished vessels can't each be labelled, so barcoding works differently: **one barcode per stack** encodes a `group_code`. `barcode_alias.target_type` is `ITEM` (boxed single-SKU, or maker EAN → add directly) or `GROUP` (stack → scan opens a "size #?" prompt; operator types 1–N → line added at that size's price). An optional `default_size_pos` lets Enter alone pick the common size. The one keyed digit is inherent — the barcode can't see which vessel was pulled.

## Maturity ladder

Each rung is a shippable release. Every column for later rungs exists from day one, nullable/dormant.

| Stage | Catalogue state | Behaviour added |
|---|---|---|
| **0 Bootstrap** | Empty (or Tally-seeded) | Normalized free-typed lines, auto-created `UNCONFIRMED` items, bill lifecycle + roles (model), weighbridge agent, manual weight fallback, structured UOM/category enums, HSN lookup table |
| **1 Accreting** | Growing, messy | Fuzzy type-ahead + near-miss nudge, monthly "items sold / purchased / new" reports, merge tool + dashboard widget, role queues, PWA for rate-approval |
| **2 Curated** | Types set, HSN/UOM, dedup'd, variant groups, item codes assigned | **GST layer slots in here.** Tally voucher push-back. |
| **2.5 Barcode** | Confirmed | SKU labels for MRP, one scanner, scan-mode billing, stack-barcode → size-number, maker-EAN capture |
| **3 Stock** | Stable | Opt-in in/out per variant, scans move stock, low-stock flags (negative allowed for BULK, reconcile) |
| **4 Inventory** | Purchase side captured | Lot labels at cutting, weighment-linked, purchase → itemise → sale, wastage, valuation, margin per item |

## Stack

Matches the fleek-backend stack so one team runs both with the same deploy,
migration and testing conventions.

- **API**: Python 3.12, FastAPI, SQLAlchemy 2.0 (typed `mapped_column`), Alembic, Pydantic v2 schemas.
- **DB**: PostgreSQL — a dedicated `metalerp` database + role on the existing shared instance, `pg_trgm` enabled. Isolated schema per developer for tests (`DB_SCHEMA=...` + `alembic upgrade head`), same rule as fleek-backend.
- **Web**: React + Vite + TypeScript, TanStack Query, React Hook Form + Zod, Tailwind.
- **PDF**: Jinja2 template → **WeasyPrint** → PDF bytes. Pure Python, no headless browser; OS deps are just `libpango` / `libgdk-pixbuf`. Bundled font with the ₹ glyph for host-independent output.
- **Invoice math**: `app/domain/tax.py`, pure functions, pytest vectors in `tests/vectors/tax_vectors.json`. A small duplicated JS helper (`web/src/lib/previewTotal.ts`) does the live-preview total and is tested against the same vectors so it can't drift.
- **Async work**: none for M1 (PDF renders synchronously in the finalize request, ~0.3–1 s). When Stage 1–2 adds Tally push / email / IRN, use a Postgres-backed `job` table polled by a thread in the same process (`SELECT … FOR UPDATE SKIP LOCKED`) — the pattern fleek-backend already uses. No Redis.

## Architecture

```
React SPA ──▶ FastAPI ──┬── Auth / tenant scope (JWT, role dependency)
                        ├── Invoice service (draft → finalize, gated, one txn)
                        ├── domain/tax.py (subtotal, discount, round-off, words; + GST branches later)
                        ├── PDF renderer (Jinja2 → WeasyPrint → PDF volume)
                        └── (Stage 1+) job table polled in-process — Tally push, IRN, EWB, email
                                    │
                    PostgreSQL (metalerp db)   PDF volume on disk (S3-compatible later)

On-prem connector agent (counter PC) — later stages:
  serial-in from weighing scale  ·  label-printer out  ·  Tally XML import + voucher push
```

- **PDF**: versioned Jinja2 template. The React live preview mirrors the same markup + imports the same CSS. `invoice.template_version` pins which template re-renders an old invoice (`v1-nongst` → `v2-gst` when GST turns on; old invoices keep their version).
- **Money**: `NUMERIC(15,2)` at the edges, integer paise (`int`) internally.
- **Numbering**: `number_sequence(tenant_id, series, fy)`, claimed with `SELECT … FOR UPDATE` in the finalize transaction, gap-free. FY = Apr–Mar. GSTN rejects gapped/duplicate doc numbers per FY, so this discipline holds from day one.
- **Tenant scope**: every tenant-scoped table carries `tenant_id`; enforced by a query-helper argument / session filter. Postgres RLS optional — app-level guard is enough for the single-tenant M1 and matches fleek's pattern.
- **Auth**: email + password (`passlib`/Argon2), JWT access token, TOTP optional. Roles `owner | accountant | viewer` now; `counter | weighbridge | rate_desk` touchpoint roles added at Stage 1.

## Data model (target)

Core (Phase 1 uses all of this):

```
tenant(id, legal_name, trade_name, pan, address, city, state_code, pincode,
       phone, email, logo_url, bank_holder, bank_name, bank_ac_no, bank_ifsc,
       bank_branch, upi_id, declaration_text, terms_text, jurisdiction_text,
       invoice_series_config, gst_enabled DEFAULT false, gstin NULL)

user(id, tenant_id, email, password_hash, role, totp_secret NULL)

party(id, tenant_id, legal_name, phone, email, pan NULL, gstin NULL,
      role ENUM('CUSTOMER','SUPPLIER','BOTH') DEFAULT 'CUSTOMER',
      default_state_code, tally_guid NULL)
party_address(id, party_id, type ENUM('BILL','SHIP','BOTH'),
              line1, line2, line3, city, state_code, pincode, is_default)

hsn_code(code PK, description, chapter, default_gst_rate, parent_code NULL,
         valid_from, valid_to NULL)          -- shipped reference list

item(id, tenant_id, group_id NULL,
     name, name_normalized,                  -- UNIQUE(tenant_id, name_normalized)
     item_type ENUM('BULK','MRP') DEFAULT 'BULK',
     category, uom, hsn_code NULL REFERENCES hsn_code(code),
     default_rate NULL, last_rate NULL, last_sold_at NULL, times_billed DEFAULT 0,
     mrp NULL, default_discount_pct NULL,
     size_pos NULL, size_label NULL,         -- variant within a group
     source ENUM('MANUAL','AUTO_FROM_INVOICE','AUTO_FROM_PURCHASE','IMPORT'),
     status ENUM('UNCONFIRMED','CONFIRMED','ARCHIVED') DEFAULT 'UNCONFIRMED',
     merged_into_id NULL, tally_guid NULL,
     -- Stage 3+: dormant
     stock_tracking DEFAULT false, stock_qty NULL, gst_rate DEFAULT 0)

item_alias(id, tenant_id, item_id, alias_text, alias_normalized)   -- UNIQUE(tenant_id, alias_normalized)
synonym(tenant_id, from_token, to_token)                            -- seeded ~30 metal-trade entries
product_group(id, tenant_id, name, category, hsn_code, uom, item_type,
              group_code TEXT UNIQUE, default_size_pos NULL)        -- table exists Phase 1, unused

invoice(id, tenant_id, doc_type ENUM('INV','CRN','DBN') DEFAULT 'INV',
        series, number NULL, fy, date,
        party_id, bill_to_addr_id, ship_to_addr_id,
        notes, terms_snapshot, declaration_snapshot,
        status ENUM('DRAFT','FINAL','CANCELLED') DEFAULT 'DRAFT',
        template_version DEFAULT 'v1-nongst', pdf_url NULL,
        -- frozen at finalize:
        subtotal, discount_total, round_off, grand_total, amount_in_words,
        -- Phase 2 dormant:
        place_of_supply_state_code NULL, supply_type NULL, reverse_charge DEFAULT false,
        taxable_total NULL, cgst_total NULL, sgst_total NULL, igst_total NULL,
        cess_total NULL, tax_in_words NULL,
        irn NULL, ack_no NULL, ack_date NULL, signed_qr NULL, signed_invoice NULL,
        ewb_no NULL, ewb_date NULL, ewb_valid_till NULL, distance_km NULL,
        transport_mode NULL, vehicle_no NULL, transporter_id NULL, gstn_status NULL)

invoice_line(id, invoice_id, sl_no, item_id NULL, description,
             hsn_code NULL, quantity, uom, unit_rate, discount, line_total,
             -- Phase 2 dormant:
             gst_rate DEFAULT 0, is_rate_inclusive DEFAULT false,
             taxable_value NULL, cgst_amt NULL, sgst_amt NULL, igst_amt NULL, cess_amt NULL,
             -- Stage 2+ dormant:
             weighment_id NULL, stock_lot_id NULL, size_pos NULL)

number_sequence(tenant_id, series, fy, last_value)   -- row-locked, gap-free
audit_log(id, tenant_id, actor_user_id, entity, entity_id, action, before_json, after_json, at)
```

Later-stage tables (not in Phase 1):

```
supplier / purchase_bill / purchase_line          -- Stage 1+ (purchase-side catalogue learning)
weighment(id, tenant_id, scale_id, gross_kg, tare_kg, net_kg, uom,
          captured_at, captured_by, raw_string, photo_url, status, invoice_line_id NULL)
scale_profile(id, tenant_id, name, make, model, connection, serial_params, parse_format, default_uom)
barcode_alias(id, tenant_id, code, target_type ENUM('ITEM','GROUP'), target_id, source)
label_batch(id, tenant_id, kind ENUM('STACK','SKU','LOT'), target_ids[], layout, created_by, created_at)
stock_lot(id, tenant_id, item_id, lot_code UNIQUE, net_kg, weighment_id, made_at, made_by, status)
kit_component(kit_item_id, component_item_id, qty)   -- only if a stack is ever sold as a set
gstn_transaction(id, invoice_id, kind, request_enc, response_enc, status, created_at)
staging_tally_item / staging_tally_ledger            -- Tally import landing
historical_sale_line(...)                            -- read-only analytics from Tally sales export
```

## Data normalization

The answer to "data that makes no sense later" — six layers, least friction first:

1. **Normalize the key, not the display.** `item.name` (as typed, printed) + `item.name_normalized` (dedupe key, hidden). Pipeline: lowercase → trim/collapse whitespace → punctuation → space → collapse again → apply synonym map → keep token order. Auto-create only if the normalized key is new.
2. **Synonym / abbreviation map** (`synonym` table, tenant-editable, ~30 seeded): `stainless → ss`, `alu/al/aluminum → aluminium`, `pcs/piece → nos`, `no./number/# → no`. Re-runnable over existing items when the map grows.
3. **Type-ahead pushes toward existing items.** `pg_trgm` fuzzy match on name + aliases. Existing match is the default (Enter picks it); "+ Create new" takes 3 keystrokes. Near-miss nudge: "Did you mean SS Balti No.3 (16×)?".
4. **Structured fields are pick-lists / lookups from day one.** UOM, category, item_type = enums. HSN = lookup against `hsn_code` (FK, nullable in Phase 1, required once GST on). Rate/weight/qty = numeric inputs. Only `name` is genuinely open.
5. **Continuous cleanup.** Dashboard "N look like duplicates" → merge queue, do a few per day. Merge is non-destructive: historical lines repoint to the survivor, losing name folds into `item_alias`. Weekly digest.
6. **Guardrails** (warnings, not blocks — except numerics): rate 0 on finalize → block; rate far off this item's history → "unusual, confirm?"; same item twice on a bill → "merge lines?"; name < 3 chars / all digits → "looks incomplete".

## Multi-touchpoint bill lifecycle (Stage 1+)

A bill is assembled by several hands over time, not one form submission:

```
DRAFT → AWAITING_WEIGHT → AWAITING_RATE → READY → FINALIZED
```

- Any line can be incomplete (weight pending, rate pending). Finalize is **gated** until every line resolves.
- Every field edit is attributed + timestamped in `audit_log` (actor + device + timestamp, server-side).
- Concurrency: last-write-wins per field with a live "X changed line 2 just now" toast; optimistic version check on finalize. Not real-time co-editing.
- Touchpoint roles: `counter` (party + items, desktop), `weighbridge` (attach weighments, tablet at scale), `rate_desk` / `owner` (set/approve ₹/kg, mobile), `viewer`. One person can hold all roles.
- API-first so a future mobile app is a peer of the web app. PWA covers the mobile-heavy tasks (approve rate, attach weight, check status) before any native build. Every mobile write carries an idempotency key.

## Weighbridge integration (Stage 0 agent, wired at Stage 1+)

- Industrial scale indicators stream weight as ASCII over RS-232. A small always-on **bridge agent** on the counter PC (Python + `pyserial`, packaged with PyInstaller and run as a Windows service via NSSM) parses it, debounces to a stable reading, stamps time + operator + scale-id + optional webcam frame, and POSTs a `weighment` to the cloud. Queues offline (local SQLite), syncs on reconnect.
- A `weighment` is standalone and immutable; billing **attaches** one to a line. Decouples the scale operator from the biller.
- No PC at the scale → serial→WiFi converter (USR-W610 / Moxa) bridges RS-232 to TCP; agent opens a socket instead of a COM port.
- Mobile can't read serial — on phones, weight always comes from the agent → cloud, and the mobile user picks a recent weighment (or photographs the display).
- Same agent hosts `/print-label` for the thermal label printer and the Tally XML bridge.

## GST layer (Phase 2)

Turned on per tenant via `tenant.gst_enabled`. Additive because the schema, paise math, frozen totals, FY-keyed numbering, versioned templates and validated HSN are all already in place.

Adds: GSTIN fields active, Place of Supply selector, per-line GST rate + "rate incl. tax" toggle, CGST+SGST vs IGST from state comparison, HSN-wise tax summary, `domain/tax.py` GST branches, INV-01 payload builder, GSP client (IRP auth → IRN / Ack / signed QR), e-Way Bill generate + Part-B update, second-page EWB layout, GSTR-1 export. All GSTN calls run through the Postgres `job` table with a per-invoice status state machine; raw request/response stored encrypted for audit.

e-Invoicing goes through a **GSP** (GST Suvidha Provider) — no direct GSTN access. GSP choice is an open decision.

## Tally integration

Tally stays the accounting book of record. Two directions, both XML-over-HTTP (Tally has no REST/JSON/OAuth/webhooks; LAN-only), routed through the connector agent for live use:

**Import (read) — Stage 0, optional.** One-time `File → Export` from Tally Prime to XML (Stock Items, Ledgers, Units, Stock Groups) — no live connection needed for the initial seed. Parse (UTF-16/BOM aware, strip control-char entities) → `staging_tally_*` → normalize → auto-confirm rows with uom + hsn + group, queue the rest. Keeps `tally_guid` per record for idempotent refresh. Changes the Stage 0 story from "empty catalogue" to "~200 items + 128 parties pre-loaded, mostly a quick confirm".

**Push (write) — Stage 2.** On finalize, enqueue a `job` that builds a Sales `VOUCHER` XML (`<VOUCHER VCHTYPE="Sales">` with `<ALLLEDGERENTRIES.LIST>` + `<ALLINVENTORYENTRIES.LIST>`) and POSTs `IMPORT DATA` to Tally `:9000` via the agent. Master-mapping table (party↔ledger, item↔stock item, tax↔duty ledgers; auto-create missing masters first; config pins Sales / tax / Round Off ledgers). Store the returned voucher GUID; idempotent on invoice-number-as-voucher-number; finalize never blocked on Tally (job retries with backoff). Errors return `<LINEERROR>`.

If the `.tsf` files on the USB are a sync snapshot rather than a restorable backup, they must be opened in a live Tally Prime first (restore a proper Backup, or select-company from a data folder), then exported to XML.

## Deployment artifacts outside the cloud app

- **Cloud app**: one FastAPI container on the existing VPS via Docker Compose, sharing the Postgres instance (own `metalerp` db) and reverse proxy (a `billing.<domain>` vhost). Web is a static Vite build served by the proxy. PDFs on a bind-mounted volume. No new datastore.
- **Connector agent** (counter PC, Stage 1+): serial-in from the scale, label-print out, Tally XML both ways. One small Python service (PyInstaller + NSSM), queues offline.
- **Serial→WiFi converter** (optional hardware, PC-less scales).
- Barcode scanners need no software (HID keyboard emulation).

# Metal ERP API

FastAPI + SQLAlchemy 2 + Alembic. Live routes: auth, firm profile,
parties (+ Tally masters import), items (+ catalogue hierarchy +
Tally stock-items import). Still to come for M1: the invoice editor,
finalize, and PDF.

Key non-obvious modules:
- `app/domain/product_parse.py` — pure rules-first parser for terse trade
  shorthand → `ParsedLine` (brand / product / sku / size / rate_mode / …).
  Acceptance corpus: `tests/fixtures/real_bill_lines.py` (19 lines from 5
  real bills). Used by the Tally item importer; will drive the catalogue-
  learning loops.
- `app/services/item_resolution.py` — `resolve_item` (exact → alias →
  pg_trgm fuzzy) **and** `resolve_group` (same ladder, one level up).
- `tools/tally_import/` — `parser.py` (party masters + stock-items +
  stock-groups), `item_match.py` (GUID→name→create ladder for items),
  `match.py` / `groups.py` (party side).

## Local development

```bash
cd api
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on POSIX
pip install -e ".[dev]"
cp .env.example .env               # edit DATABASE_URL etc.
```

Bring up a local Postgres (or point `DATABASE_URL` at one), then:

```bash
alembic upgrade head
python -m app.seed                 # HSN codes + synonyms; +tenant if SEED_* env set
uvicorn app.main:app --reload
```

`GET http://localhost:8000/health` → `{"status": "ok", ...}`
API docs at `/api/docs`.

## Checks

```bash
ruff check .          # lint
mypy app              # type-check
pytest -q             # tests (in-memory SQLite; CI also runs against real Postgres)
```

## Migrations

```bash
alembic revision --autogenerate -m "what changed"   # generate
alembic upgrade head                                 # apply
alembic downgrade -1                                 # roll back one
alembic check                                        # models vs migrations drift
```

- `0001` — full schema (all tables; GST/stock/barcode columns present,
  nullable, dormant).
- `0002` — `pg_trgm` extension + GIN trigram indexes on
  `item.name_normalized` and `item_alias.alias_normalized` (Postgres
  only; the `metalerp` database is created with the extension).
- `0003` — `party.status` (active/archived), `party.source` + `source_ref`
  (provenance), `party.last_txn_at` (dormancy), `tenant.dormant_party_days`
  (default 180), and a `pg_trgm` GIN index on `party.legal_name` (Postgres
  only) for fuzzy party search.
- `0004` — inward bill import: `inward_bill*`, `supplier_template`,
  `tally_ledger_config`, `extraction_run`, `job` tables + `tenant.ext_inward_import`.
- `0005` — `staging_tally_party`: holds a parsed Tally masters-XML party
  import between upload and commit. No changes to `party`.
- `0006` — `item`: metal-trade attributes (metal/shape/grade/size_text/
  thickness/width/length_mm/finish), units & conversion (secondary_uom/
  conversion_factor/weight_per_uom/purchase_uom), price band (price_min/
  price_max), notes; + dormant columns for the price engine
  (markup_pct/suggested_rate/…/price_review_pending) and Stage 2/3
  (barcode/sku/reorder_level). `tenant.default_markup_pct`.
- `0007` — item hierarchy: per-tenant `item_category` (seeded on
  register), `product_group.category_id` + `default_rate_mode` +
  `name_normalized`, `item.category_id` + `rate_mode` + `weight_per_piece`,
  `item_alias` gains `group_id` (xor with `item_id`) + `source` +
  `last_used_at` for the catalogue-learning loops.
- `0008` — `staging_tally_item`: holds a parsed Tally stock-items XML
  import between upload and commit. No changes to `item`. Commit also
  resolves-or-creates `product_group`s from the Tally Stock Groups so
  an import lands in the tree. Parses the **Masters** XML shape
  (`<STOCKITEM>` w/ GUID/HSNCODE/BASEUNITS/PARENT); a **Stock Summary**
  (`StkSum`, `<DSPACCNAME>`/`<DSPSTKINFO>`) report is not yet supported.
- Deferred (with catalogue-learning Loop 1): `inward_bill_line.group_id`
  + `inward_line_size` for pooled-weight inward lines.

## Layout

```
app/
  main.py            FastAPI app (+ /health)
  config.py          Settings from env
  db.py              engine, session factory, Base
  models/            SQLAlchemy models (full schema)
  seed.py            idempotent reference-data + first-tenant seed
  domain/            pure calculation (tax.py — Phase 2)
  routers/           HTTP routes (Phase 3+)
  services/          finalize, numbering, item-accretion, pdf (Phase 6-7)
  templates/         invoice HTML/CSS for WeasyPrint (Phase 7)
alembic/             migrations
tests/
```

## Deployment

Built and run as the `metalerp-api` container in the shared `fleek-stack`
Docker Compose project (infra repo). The container runs
`alembic upgrade head` before binding. See the infra repo's
`metalerp/Dockerfile` and `webhook/scripts/deploy-metalerp.sh`.

# Metal ERP API

FastAPI + SQLAlchemy 2 + Alembic. Milestone 1 delivers the data model
and a `/health` route; the domain routes (party, item, invoice, finalize,
PDF) land in later phases.

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

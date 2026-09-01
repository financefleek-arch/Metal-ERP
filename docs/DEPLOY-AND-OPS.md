# Deploy & Ops

How Metal ERP is deployed on the shared **fleek-stack** VPS, and the
setup footguns that were hit (and fixed) getting it there.

Metal ERP is **one more service in fleek-stack**, not a separate stack:
one FastAPI container, the shared Postgres instance (own `metalerp` DB),
the shared Caddy reverse proxy (a `metal.fleekfinance.in` vhost), and the
shared webhook receiver.

---

## Topology

| Piece | Where | Notes |
|---|---|---|
| `metalerp-api` container | fleek-stack, host port `127.0.0.1:8800` → container `:8000` | `python:3.12-slim`; CMD runs `alembic upgrade head` then `uvicorn app.main:app` |
| Database | shared `postgres` container, DB `metalerp` | `pg_trgm` enabled; connects as the admin role, same as advisoros/fleek-backend |
| Frontend | static Vite build, served by Caddy from `/srv/metalerp-frontend` | built by `deploy-metalerp.sh` via `docker build --target build` + `docker cp` |
| Reverse proxy | shared `caddy` | `metal.fleekfinance.in` → `/api/*` proxied to `metalerp-api:8000`, else the SPA |
| PDFs | `metalerp_pdfs` named volume | WeasyPrint output; not object storage for M1 |
| Inward files | `metalerp_inward` named volume (`INWARD_DIR=/data/inward`) | `ext_inward_import` only. Uploaded supplier PDFs at `/data/inward/pdf/<bill-id>.pdf`, **deleted once their Tally XML is built** (re-uploadable). Generated XML at `/data/inward/xml/inward-<bill-id>.xml` — the durable artefact, re-downloadable. A re-upload of the same invoice (same tenant + supplier GSTIN + bill no) folds into the existing row and discards stale XML. |
| Secrets | Vault KV `secret/metalerp/core#jwt_secret` | `BREVO_API_KEY` shared; `METALERP_SENTRY_DSN` plain `.env` |

The **infra-repo** side of the wiring lives in `fleek-infra`:
`docker-compose.yml` (the `metalerp-api` service + the caddy volume mount),
`metalerp/Dockerfile`, `caddy/Caddyfile`, `postgres/init/01-create-databases.sql`,
`postgres/backup.sh`, `webhook/hooks.json.template`, `webhook/ssh_config`,
`webhook/scripts/deploy-metalerp.sh`, and a `metalerp_image_changed` branch
in `webhook/scripts/deploy-fleek-infra.sh`.

---

## Push-to-deploy

Push to `Metal-ERP` `main` → GitHub webhook → `deploy-metalerp.sh` (in
the webhook container):

1. lock, deploy-log with a success/failure email trap
2. `git pull --ff-only` the Metal-ERP checkout at `/opt/fleek-stack/metalerp/metalerp/`
3. **SPA build** — `docker build --target build` in `web/`, then `docker cp`
   the `dist/` out (the webhook image has only the Docker CLI, no node —
   same trick as `fleek-frontend/build.sh`). **Guarded**: skipped if
   `web/Dockerfile` is absent.
4. source `.env` + `load-vault-secrets.sh`, then
   `docker compose up -d --build --no-deps metalerp-api`
5. **migrations run inside the container CMD** — `deploy-metalerp.sh` does
   *not* also run `alembic upgrade head` (see gotcha #4)
6. `docker compose restart caddy` — only when the SPA was rebuilt
7. `wait-for-healthy metalerp-api` (`/health` does a live DB `SELECT 1`)

Downloadable-XML fallback path etc. are app features, not deploy.

---

## One-time VPS setup (already done — recorded for a rebuild)

```bash
# 1. Database
cd /opt/fleek-stack
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE metalerp;"'
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d metalerp -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"'
#   use $POSTGRES_USER INSIDE the container — sourcing .env in a host shell
#   hits "role root does not exist" when $POSTGRES_ADMIN_USER is empty.

# 2. Deploy key for the webhook container
ssh-keygen -t ed25519 -f /opt/fleek-stack/webhook/ssh/metalerp_key -N "" -C metalerp-deploy
cat /opt/fleek-stack/webhook/ssh/metalerp_key.pub
#   → register on github.com/financefleek-arch/Metal-ERP → Settings → Deploy keys
#     (read-only). Also add a `metalerp-github` Host alias to the `deploy`
#     user's own ~/.ssh/config for the initial clone.

# 3. Clone the app repo to the path the compose build context expects
mkdir -p /opt/fleek-stack/metalerp
cd /opt/fleek-stack/metalerp
git clone git@metalerp-github:financefleek-arch/Metal-ERP.git metalerp

# 4. Vault: write the JWT secret (see the RUNBOOK section
#    "metalerp/core: new KV path for Metal ERP — 2026-08-31" for the
#    policy-extension steps — read the LIVE policy, append one line, verify)
export JWT=$(openssl rand -hex 32)          # EXPORT — a bare var stores blank
docker exec -e VAULT_TOKEN -e JWT vault sh -c 'vault kv put secret/metalerp/core jwt_secret="$JWT"'
unset JWT
docker exec -e VAULT_TOKEN vault vault kv get -format=json secret/metalerp/core | jq -r '.data.data | keys[]'

# 5. Webhook container must be REBUILT to pick up the new hook
#    (hooks.json.template is COPY'd into the image at build time)
cd /opt/fleek-stack
set -a && . .env && set +a
export VAULT_ADDR=http://127.0.0.1:8700
. fleek-infra/scripts/load-vault-secrets.sh   # WEBHOOK_SECRET is Vault-sourced
docker compose up -d --build --force-recreate webhook

# 6. GitHub webhook on Metal-ERP →
#    https://deploy.fleekfintech.com/hooks/deploy-metalerp
#    content-type application/json, secret = the same GITHUB_WEBHOOK_SECRET,
#    push events only.

# 7. metal.fleekfinance.in A record → the VPS IP (done — same as api/advisor/client)

# 8. First build + the caddy mount
docker compose up -d --build metalerp-api
docker compose exec -T metalerp-api python -m app.seed        # HSN + synonyms
docker compose up -d --force-recreate --no-deps caddy         # NOT restart — a
#    new volumes: entry isn't applied by restart. Vault sourced (step 5) so
#    ytpipe/builds basicauth hashes don't blank.

# 9. Adding the metalerp_inward volume to an already-running stack needs a
#    recreate (a `volumes:` change is not applied by `restart` or plain `up -d`):
docker compose up -d --force-recreate --no-deps metalerp-api

# 10. Turn on Inward Bill Import for a tenant (no admin UI yet):
docker compose exec -T postgres psql -U "$POSTGRES_ADMIN_USER" -d metalerp \
  -c "UPDATE tenant SET ext_inward_import = true WHERE legal_name = '<Firm Name>';"
#    Then hard-refresh the browser so the SPA re-reads /auth/me.
```

---

## Setup gotchas (every one hit this session)

**1. Webhook image bakes `hooks.json.template` — `restart` never loads a new hook.**
`entrypoint.sh` renders `/etc/webhook/hooks.json` from a template `COPY`'d in at
build time. Symptom: delivery 200s but `docker exec webhook cat /etc/webhook/hooks.json | grep deploy-metalerp` is empty. Fix: `docker compose up -d --build webhook`.

**2. `GITHUB_WEBHOOK_SECRET` is Vault-sourced.** A manual `docker compose up -d --build webhook`
without first sourcing `.env` + `load-vault-secrets.sh` rebuilds webhook with
`WEBHOOK_SECRET=""` → hooks.json gets `"secret": ""` → every delivery returns
**500 "Error occurred while evaluating hook rules"**. Verify:
`docker exec webhook sh -c 'printf %s "$WEBHOOK_SECRET" | wc -c'` (non-zero).

**3. The `+x` bit on `deploy-metalerp.sh` must be committed as `100755`.**
Windows drops it. Symptom: webhook log `error in exec: "…deploy-metalerp.sh": permission denied`.
Fix: `git update-index --chmod=+x …` then `git -c core.filemode=false commit`;
check `git ls-tree HEAD …` shows `100755`. A mode-only pull may not apply on the
VPS — `chmod +x` the file directly there.

**4. Don't run `alembic upgrade head` twice on a fresh DB.** The container CMD
already does it; if `deploy-metalerp.sh` also does, both race to
`CREATE TABLE alembic_version` → one gets a `UniqueViolation`, that process exits
1, the deploy is FAILED and emails — **but the DB is migrated fine**. The deploy
script only tails the container logs; `wait-for-healthy` is the real check.

**5. Caddy `volumes:` changes need `--force-recreate`, not `restart`.** The
`./metalerp-frontend/dist:/srv/metalerp-frontend:ro` mount didn't appear inside
Caddy after `restart`. Fix: `docker compose up -d --force-recreate --no-deps caddy`
(Vault sourced). Once the mount exists, `restart` re-reads the bind content fine.

**6. Vault `kv put field="$VAR"` stores blank unless `$VAR` is exported.** `-e VAR`
on `docker exec` only forwards exported vars. `vault kv get` shows the empty value
as `n/a`. Always `export` first; verify with `-format=json | jq -r '.data.data.<field>'`.

**7. `/api/health` vs `/health`.** The app serves both — `/health` is the internal
Docker/`wait-for-healthy` check (hits the container directly on `:8800`);
`/api/health` is the one reachable through Caddy. A bare `/health` through
`metal.fleekfinance.in` 404s because Caddy only proxies `/api/*`.

---

## Runbook

**Restart the API:** `cd /opt/fleek-stack && docker compose restart metalerp-api`

**Redeploy manually** (Vault sourced first):
```bash
cd /opt/fleek-stack && set -a && . .env && set +a
export VAULT_ADDR=http://127.0.0.1:8700 && . fleek-infra/scripts/load-vault-secrets.sh
docker compose up -d --build --no-deps metalerp-api
```

**Run migrations by hand:** `docker compose exec -T metalerp-api alembic upgrade head`

**Check the schema version:**
```bash
docker compose exec -T metalerp-api python -c "
from sqlalchemy import create_engine, text
from app.config import get_settings
print(create_engine(get_settings().database_url).connect().execute(text('SELECT version_num FROM alembic_version')).scalar())"
```
(`psql` is not in the slim image — use Python.)

**Seed reference data:** `docker compose exec -T metalerp-api python -m app.seed`

**Deploy log:** `/opt/fleek-stack/logs/metalerp-api/deploy.log` (last run only,
START/SUCCESS or START/FAILED wrapped).

**Backups:** `metalerp` is in `postgres-backup`'s nightly `pg_dump` → R2 loop.

# EXECUTION PLAN — WhatsApp delivery tracking (webhook fan-in)

Status: **BUILT 2026-09-08, UNCOMMITTED across 3 repos, NOT deployed.**
Slice of S1 / F2-finish in `MASTER-tally-complement-saas.md`. No migration.
Backend suite green (388 pass / 1 skip, +3 webhook tests). NOT run e2e
against real Meta. UI (status badge + message log) is a **separate**
follow-up slice, not in this one.

## Problem

Metal ERP sends invoices on WhatsApp but every `whatsapp_message` row stays
at `sent` forever — Meta delivers delivery/read/failed callbacks to **one**
webhook URL (fleek-backend's `POST /api/whatsapp/webhook`), never to
metalerp. Metal ERP's `handle_status_webhook` + HMAC-verified endpoint
already exist; nothing feeds them.

## Design: fleek-backend fans out the raw body

Meta signs every webhook POST body with the shared **FleekWA App Secret**
(`fleek-backend/core#whatsapp_app_secret`). fleek-backend and metalerp use
the *same* secret, so after fleek-backend verifies + handles a callback it
re-POSTs the **raw bytes + the original `X-Hub-Signature-256` header** to
`metalerp-api`. Metal ERP re-verifies independently — no trust relationship,
no new secret. Fire-and-forget: Meta must still get its 200, and a dropped
status just leaves an invoice at `sent`.

Metal ERP's exclusive `invoice_ready` template + per-firm `phone_number_id`
mean every row in metalerp's `whatsapp_message` is a metalerp send, and the
firehose of fleek-backend statuses simply misses on `wa_message_id` and is
ignored (existing behaviour).

## Changes (all uncommitted)

### fleek-infra  (`c:\Fleek Finance Code\infra`, branch `master` — push FIRST)

- **`scripts/load-vault-secrets.sh`** — new hard export
  `METALERP_WHATSAPP_APP_SECRET` ← `fleek-backend/core#whatsapp_app_secret`
  (same field fleek-backend's webhook reads). Stale comment about the field
  "never written to Vault" replaced. Log line 35 → 36 variables.
- **`docker-compose.yml`** —
  - `metalerp-api` service: `WHATSAPP_APP_SECRET: ${METALERP_WHATSAPP_APP_SECRET}`
  - `fleek-backend` service: `METALERP_WHATSAPP_WEBHOOK_URL:
    http://metalerp-api:8000/api/whatsapp/webhook` (internal Docker network,
    not via Caddy; unset to disable fan-out).
- `.env.example` — no change (no `METALERP_*` vars are listed there; the URL
  is a non-secret compose default like `ADVISOROS_BASE_URL`).

### fleek-backend  (`c:\Fleek Finance Code\FinancialPlanning\fleek-backend`)

- **`routes/intelligence.py`** — `whatsapp_webhook()` calls a new
  `_fanout_whatsapp_webhook(body, sig)` after its own
  `handle_status_webhook`, before `return 200`. Helper: `requests.post(url,
  data=body, headers={Content-Type, X-Hub-Signature-256: sig}, timeout=3)`;
  no-op when `METALERP_WHATSAPP_WEBHOOK_URL` unset; all exceptions logged and
  swallowed. `body`/`sig` are already local vars, assigned before the HMAC
  branch, so reaching the call means the signature was valid (or non-prod
  with no secret). `requests` is already a dep (2.33.1).

### Metal ERP  (`c:\Fleek Finance Code\Metal ERP`)

- **`api/app/services/whatsapp.py`** — `handle_status_webhook` docstring
  notes the fan-in. New `_fmt_wa_error(err)` turns Meta's status-error dict
  into `"Message undeliverable (code 131026) - Receiver is not a valid
  WhatsApp user"` instead of dumping the raw dict into `msg.error`.
  (`send_invoice` opt-in removal is the *other* slice —
  `EXECUTION-PLAN-phone-input-and-save.md` — same file, same uncommitted
  batch.)
- **`api/tests/test_whatsapp_send.py`** — +3 tests: matching wamid advances
  `sent → delivered → read`; unknown wamid is a silent no-op; `failed` with
  an `errors[]` records a short readable string (no `{` dict dump).

## Deploy order

1. Push **fleek-infra** `master`. Webhook redeploys both services; the
   deploy sources `load-vault-secrets.sh` so `METALERP_WHATSAPP_APP_SECRET`
   and `METALERP_WHATSAPP_WEBHOOK_URL` land in the containers.
2. Push **fleek-backend** — fan-out goes live.
3. Push **Metal ERP** — `_fmt_wa_error` (and the phone slice).
   `alembic upgrade head` runs on container start; **no migration here**.

Order 1→2 matters: if fleek-backend ships the fan-out before metalerp has
`WHATSAPP_APP_SECRET`, metalerp's webhook 503s in prod on every forwarded
call (fleek-backend swallows it — harmless, just no tracking yet).

## Verification after deploy

- Send a real invoice on WhatsApp from a connected firm.
- `docker logs metalerp-api` shows the forwarded POSTs; no 401/503.
- The `whatsapp_message` row moves to `delivered` (and `read` if the
  recipient has receipts on) within a few seconds.
- Send to a number that isn't on WhatsApp → row goes `failed` with a
  readable `error`.

## Not in this slice

- **Status badge + per-invoice message log + resend button** — the UI. Next
  F2-finish slice.
- Retry/durability for a dropped fan-out POST (would need a queue). A missed
  status is non-fatal for now.
- `message_template_status_update` fan-out (template paused/quality alerts).

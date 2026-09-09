# EXECUTION PLAN — Tally Agent: seamless shop onboarding + health reporting

Status: **BUILT 2026-09-09, NOT committed / NOT deployed** (user does
check-ins). Visual review: `docs/visual-plan/tally-agent-onboarding-review.html`.
Builds on the deployed F1a slice (`EXECUTION-PLAN-F1a-tally-masters-in.md`) +
the F10 companion-agent platform (`tally-agent-backup-slice`).

Backend (452 tests pass, 2 skip — up from 424 at F1a's commit), .NET agent
(`dotnet build` clean, no test project — matches F1a's own verification
level), and web (`tsc`/`eslint`/`vite build` all clean) are done per the
build order below. **Gaps before this works live:** (1) the prod
`tally_agent_build_dir` volume needs a real `dotnet publish -r win-x64
--self-contained` drop — nothing has been published yet; (2) `settings.base_url`
needs confirming/fixing for prod in `fleek-infra` (push infra first if wrong);
(3) no live end-to-end run — same gap F1a itself still has, now compounded
with "does the zip actually install and phone home on a real Windows box".

## Goal (locked in the review)

Provisioning a firm's agent **is** the installer. One click in the platform-admin
console: the backend mints the shop key, injects it into `appsettings.json`,
packs a zip (published agent build + `install.ps1` + a setup PDF), caches it
against the firm, and discards the plaintext key. From then on the operator has a
**Download** button + a shareable link; the firm's own login has a **Connect your
Tally** card serving the same cached zip. The shop unzips, right-clicks
`install.ps1` (zero-argument, self-elevating), flips one Tally toggle. The agent
then checks in every 60s with **two independent signals** — `agent → Fleek`
(proves the install + key) and `agent → TallyPrime` (`tally_reachable` + reason).

Nobody hand-edits a config file. Nobody assembles a zip by hand.

## Decisions (from the review)

1. **Both** parties can download: operator (platform-admin route) and the firm's
   own `WriteUser` session (tenant-scoped route). Same cached artifact.
2. **Cached on provision, rebuilt on key-rotate.** Not per-request.
3. **Cache lives in R2** (reuse `tally_r2_*` creds/bucket under a
   `installers/<shop_id>.zip` key) — not a `bytea` column.
4. Tenant-scoped download route, **no extra token**. Rotating the key is the
   "revoke a leaked zip" lever.
5. Backup watch-folder path: **baked default, silent**, console-overridable later.
6. "Offline" = red at **5 min** no checkin. **Dashboard-only** for the pilot (no
   push/email).
7. **No auto-update** for the pilot — re-run a newer `install.ps1`.

## Migration

Head is `0025`. This slice adds **`0026`** — three nullable columns on
`backup_shop`:
- `installer_r2_key` VARCHAR(500) NULL — the cached zip's R2 key
- `last_tally_ok_at` DATETIME(tz) NULL — last checkin where `tally_reachable`
- `last_tally_status` VARCHAR(40) NULL — `connected` | `refused` |
  `no_company` | `unknown` | null (never reported)

`_JSON` not needed. `down_revision = "0025"`.

---

## Work breakdown

### A. Backend — assembly + serving

**A1. `_generate_appsettings()` — add the `TallyMasters` block.**
`api/app/services/tally_agent_installer.py::_generate_appsettings` currently
writes `BackupSync` / `BackupHealthMonitor` / `WhatsAppDelivery`. Add:
```json
"TallyMasters": {
  "Enabled": true,
  "GatewayUrl": "http://localhost:9000",
  "ExportDir": "",
  "PollIntervalMinutes": 1
}
```
Matches `AgentOptions.TallyMastersOptions` defaults exactly.

**A2. `build_installer_zip()` — rename the README to a real doc + keep it as
`Setup guide.txt` for now** (a true PDF is a follow-up; the review shows a PDF
but a plain-text guide unblocks the pilot). Leave the zip layout otherwise as
built (`publish/` + `install.ps1` + guide). No functional change beyond A1.

**A3. `settings.public_base_url`.** `config.py` already has `base_url`
(`http://localhost:8000`, line 18) — reuse it, no new setting. The installer's
`backend_base_url` = `get_settings().base_url`. **Infra note:** confirm
`BASE_URL` / `base_url` is set to the real prod URL in `fleek-infra` compose for
`metalerp-api` (it drives other absolute-URL features already, so likely fine —
verify, don't assume). No new env var → no infra push needed for this unless
`base_url` is currently wrong in prod.

**A4. R2 helpers.** `backup_storage.py` has `presigned_put_url` + `get_object`.
Add:
- `put_object(r2_key: str, body: bytes, content_type: str) -> None` — direct
  server-side upload (the zip is built in the API process, not the agent, so no
  presign needed).
- `delete_object(r2_key: str) -> None` — for rotate (best-effort; overwrite is
  also fine since the key is deterministic).
Both raise `R2NotConfigured` consistently.

**A5. Build-and-cache helper.**
New `api/app/services/tally/installer.py`:
```python
def build_and_cache_installer(session, shop: BackupShop, *, plaintext_key: str) -> str:
    """Build the per-shop zip and store it in R2. Returns the r2_key.
    Called right after a key is minted (provision) or rotated — the only
    two moments the plaintext key exists."""
    zip_bytes = build_installer_zip(
        shop_api_key=plaintext_key,
        backend_base_url=get_settings().base_url,
    )
    r2_key = f"installers/{shop.id}.zip"
    put_object(r2_key, zip_bytes, "application/zip")
    shop.installer_r2_key = r2_key
    session.flush()
    return r2_key
```
`BuildNotAvailable` (raised by `build_installer_zip` when
`tally_agent_build_dir` is empty) propagates — the caller turns it into a 503
with a clear message so the operator knows the prod build hasn't been dropped
yet.

**A6. Wire into provision + rotate (`routers/admin.py`).**
- `provision_firm_tally_shop`: after `make_shop(...)` returns the key, call
  `build_and_cache_installer(session, shop, plaintext_key=key)`. Catch
  `BuildNotAvailable` → `raise HTTPException(503, "Agent build not published
  yet — contact platform ops")` **but still return the provision result** (the
  agent identity exists; the zip can be built later by rotate). Actually
  simpler: build first, and if it fails, 503 and let the operator retry — the
  `make_shop` row is committed either way and rotate will pick it up. Decide in
  code review; lean toward "503 but identity persists, rotate rebuilds".
- `rotate_firm_tally_shop_key`: same — after the new key, rebuild the cache.
- `FirmTallyShopProvisionResult`: **drop `api_key` from the response** (or keep
  it but the UI stops showing it). The key no longer needs to reach a human —
  it's in the zip. Keep `shop_id`, `created`. Add `installer_ready: bool`.
  → **Decision:** keep `api_key` in the schema for one release (tests + the
  emergency "read the key" path), mark it deprecated in the docstring; the UI
  drops the copy-box. Revisit removing it entirely in a later slice.

**A7. `FirmTallyShopOut` — add health fields.**
`provisioned`, `shop_id`, `is_active`, `last_checkin_at`, `last_upload_at`
(existing) **plus**:
- `installer_ready: bool` (`shop.installer_r2_key is not None`)
- `agent_online: bool` (`last_checkin_at` within 5 min)
- `tally_status: str | None` (`shop.last_tally_status`)
- `tally_ok_at: datetime | None` (`shop.last_tally_ok_at`)
`_shop_out()` fills them.

**A8. Download routes.**
- Platform-admin: `GET /api/admin/firms/{firm_id}/tally-shop/installer` in
  `admin.py` — load firm → its `BackupShop` → 404 if no `installer_r2_key` →
  `get_object(key)` → `StreamingResponse(io.BytesIO(bytes), media_type=
  "application/zip", headers={"Content-Disposition": f'attachment;
  filename="tally-agent-{slug}.zip"'})`. `slug` from the firm's legal name.
- Firm's own: `GET /api/tally/installer` in `routers/tally.py` (`WriteUser`) —
  resolve `BackupShop` by `tenant_id == current_user.tenant_id` → same stream.
  404 with "Ask Fleek to set up Tally sync" if none.

**A9. `checkin` — accept the two new fields.**
`schemas_tally_agent.ShopCheckinIn`: add
```python
tally_reachable: bool | None = None
tally_reason: str | None = None   # 'connected'|'refused'|'no_company'|'unknown'
```
`routers/tally_agent.py::checkin`: if `body.tally_reachable is not None`:
- `shop.last_tally_status = body.tally_reason or ("connected" if body.tally_reachable else "unknown")`
- if `body.tally_reachable`: `shop.last_tally_ok_at = now`
Keep everything else (outbox dispatch) unchanged.

**A10. "Pull masters" gate.**
`routers/tally.py::pull-masters` (or wherever `enqueue_pull_masters` is called):
before enqueue, if the firm's shop `last_tally_status not in (None, "connected")`
**and** `last_checkin_at` is recent → `raise HTTPException(409, "Tally isn't
reachable from the shop's agent right now (<reason>). Ask the shop to open
TallyPrime with the company loaded.")`. If `last_checkin_at` is stale, a
different 409 ("the shop's agent is offline"). Don't hard-block on a *stale*
`connected` — only on a fresh non-connected.

### B. Agent (.NET) — the second signal

**B1. `TallyMastersModule` (or a tiny always-on probe).**
The module already talks to the gateway during a pull. Add a lightweight
reachability probe every run even when there's no job:
- `RunOnceAsync`: at the top, do a cheap gateway call (an empty/`$$` export or
  a HEAD-equivalent). Classify:
  - HTTP 200 + `<STATUS>1</STATUS>` or any well-formed Tally response →
    `connected`
  - body contains `Could not find Company` → `no_company`
  - `HttpRequestException` / connection refused / timeout → `refused`
  - anything else → `unknown`
- Stash on `AgentContext`: `ctx.SetTallyReachable(bool, reason)`.

**B2. `AgentContext`** — `bool? TallyReachable`, `string? TallyReason`,
`SetTallyReachable(bool, string)`.

**B3. `TallyAgentService.ExecuteAsync`** — pass
`ctx.TallyReachable`, `ctx.TallyReason` into `backend.CheckinAsync(...)`.

**B4. `BackendClient.CheckinAsync` + `CheckinRequest`** (`BackendModels.cs`) —
add `[JsonPropertyName("tally_reachable")] bool? TallyReachable` and
`[JsonPropertyName("tally_reason")] string? TallyReason`.

### C. `install.ps1` — zero-argument, self-elevating

Full rewrite of `tally-agent/install.ps1`:
1. **Params all optional.** `[string]$ShopApiKey = ""`, etc.
2. **Self-elevate:** if not admin, `Start-Process powershell -Verb RunAs
   -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" ` and exit.
   (Re-launch with the same args; the elevated instance re-runs from the top.)
3. **Skip the key overwrite** when the bundled `publish/appsettings.json`
   already has a real `Agent.ShopApiKey` (non-empty, not a known placeholder
   like `REPLACE_ME`). Only overwrite from params if a param was actually
   passed AND the bundled value is a placeholder.
4. **Keep** the copy-to-`Program Files` + `New-Service` + `Start-Service` flow
   (it's fine).
5. **After start:** ping the backend once (`Invoke-RestMethod` to
   `<BackendBaseUrl>/api/tally-agent/checkin` won't work without the key
   header + module body — simpler: just wait ~5s and report "service running;
   it will check in within a minute" OR read the service's first log line).
   Minimal: print "Service started. Check the Fleek console in ~1 minute to
   confirm it connected."
6. **Header doc-comment:** drop the `tools.make_backup_shop` reference; say the
   key is pre-baked by the download.
7. **Strip all non-ASCII** — no em-dashes, smart quotes. Save UTF-8. (PS 5.1
   BOM/codepage gotcha bit this before: a stray `—` broke brace matching with
   a misleading error.)

### D. Frontend

**D1. Ops console — `web/src/pages/admin/TallyPanel.tsx`.**
- **Not set up:** one primary button "Set up Tally sync" → `POST
  .../tally-shop`. On 503 (build not published): inline error "The agent build
  hasn't been published to production yet."
- **Set up:** an **Installer** sub-block — Download button (hits
  `GET .../tally-shop/installer`, triggers a browser download), "Copy link"
  (copies the same URL), "Rotate key & rebuild" (→ `POST
  .../tally-shop/rotate-key`, confirm dialog "the old download stops working").
  **Drop the copy-once key box.**
- A **Live health** sub-block — two cells: *Agent → Fleek* (Online / Offline
  from `agent_online`) and *Agent → TallyPrime* (Connected / Not reachable +
  reason from `tally_status`). Plus last pull, agent version if available.
- Poll `GET .../tally-shop` every 20s while the pane is open.

**D2. Shop dashboard — "Connect your Tally" card.**
New component, shown on the firm's own dashboard (or Settings) when their
`GET /api/tally/company` / a new `GET /api/tally/agent-status` says not
connected. Download button → `GET /api/tally/installer`. One-line reminder
about the "acts as Server" toggle. Turns green when `tally_status ==
"connected"`.
→ **Scope call:** D2 can be a **fast-follow** if it risks bloating this slice.
The operator can forward the link (D1's Copy link) in the meantime. Flag in
review.

**D3. "Pull masters" button** — disabled with the inline 409 reason from A10.

### E. Tests

- `test_tally_agent_installer.py` (new): `_generate_appsettings` includes
  `TallyMasters`; `build_installer_zip` raises `BuildNotAvailable` on an empty
  build dir; with a stub build dir → zip contains `publish/appsettings.json`
  with the key + `install.ps1` + guide.
- `test_tally_connector.py` / `test_tally_agent.py`: provision now also caches
  an installer (monkeypatch R2 `put_object`); `GET .../tally-shop/installer`
  streams it; 404 before provision; rotate rebuilds (new bytes).
  `GET /api/tally/installer` tenant-scoped: firm A can't get firm B's zip.
- checkin: `tally_reachable=false, tally_reason="refused"` → `last_tally_status`
  set, `last_tally_ok_at` untouched; `=true` → both set.
- pull-masters: fresh `refused` → 409; fresh `connected` → proceeds; stale
  checkin → offline 409.
- .NET: `AgentContext.SetTallyReachable` round-trips into the checkin body;
  gateway "Could not find Company" → `no_company`; connection refused →
  `refused`.

### F. Deploy / infra

1. **Confirm `base_url`** is the real prod URL for `metalerp-api` in
   `fleek-infra` compose. Fix + push `fleek-infra` first if wrong.
2. **Publish the agent build to prod:** `dotnet publish TallyAgent -c Release
   -r win-x64 --self-contained -o publish` → drop at
   `/data/tally-agent-build` (the `tally_agent_build_dir` volume). Confirm the
   volume mount exists in compose; add it if not (infra push).
3. Metal ERP deploy — `alembic upgrade head` runs `0026`.
4. R2: `installers/` prefix in the existing `tally_r2_bucket` — no new bucket.

---

## Build order

A1 → A2 → A4 → A5 → A3 → A6 → A7 → A8 (backend assembly + serving, testable
with R2 monkeypatched) → A9 → A10 (health signal) → C (install.ps1, no build
dep) → B (agent .NET) → E (tests alongside each) → D (frontend, after backend
is green) → F (deploy).

## Not in this slice

- A real PDF setup guide (plain-text guide ships; PDF is a follow-up).
- Auto-update / self-update of the agent.
- Push/email alerting on "agent offline".
- Any voucher push (F1b).
- Multi-company per firm.

---

## Build log — 2026-09-09

Built in one session, backend-first per the plan's build order. Deviations
from the original plan, decided along the way:

- **D2 (shop-facing "Connect your Tally" card) was built, not deferred** —
  `web/src/components/TallyConnectCard.tsx`, mounted on `FirmPage.tsx` (the
  firm's own "Firm profile" settings page). New tenant-scoped
  `GET /api/tally/agent-status` (no key exposure) backs it; it self-hides
  once `tally_status === "connected"`.
- **"Copy link" dropped from the operator UI.** The download route needs a
  bearer JWT (same-origin auth model, no anonymous/signed-URL access), so a
  plain copyable URL can't actually work for someone without a login —
  reintroducing that would mean the token machinery the design deliberately
  avoided. Operator downloads the zip and forwards the *file* by hand
  (email/WhatsApp) instead. `FirmTallyShopOut`/`FirmTallyShopProvisionResult`
  schemas still support a future signed-link addition if this proves
  friction-heavy with real pilots.
- **`FirmTallyShopProvisionResult.api_key` kept as a deprecated, always-empty
  field** rather than removed outright — avoids an FE type-shape break for
  one release; the console no longer reads it (no key box in the UI).
- **A10's reachability gate covers `pull-masters` retry too**, not just the
  initial call — a retry after the shop fixes the "Tally closed" state
  shouldn't dispatch a job that's certain to sit on "waiting on Tally" again.
- **A10 defers to `enqueue_pull_masters`'s existing 422** when
  `company.shop_id is None` (company registered before any agent exists) —
  kept that as the more specific error rather than letting the new "no
  agent" 409 shadow it.
- **New `services/tally/agent_health.py`** (not anticipated by name in the
  plan) holds `AGENT_OFFLINE_AFTER` + `agent_online()` + `tally_reachable_now()`
  as the one shared definition of "online" — used by `admin.py` and
  `tally_self_serve.py` `_shop_out`-equivalents and by the `tally.py` pull
  gate, so the 5-minute threshold lives in exactly one place.
- **New router `app/routers/tally_self_serve.py`** (`/api/tally/*`,
  `WriteUser`) rather than adding routes to the platform-admin-gated
  `tally.py` — that router is fully `require_platform_admin`-scoped at the
  `APIRouter` level, so the tenant-scoped surface needed its own file.
- **`.NET`: no test project exists for `tally-agent/`** (confirmed — none in
  the repo). Verification is `dotnet build` clean, matching how F1a itself
  shipped ("web tsc/lint/build clean; agent dotnet build clean", no agent
  unit tests). `TallyGatewayClient.ProbeAsync` classifies connected /
  no_company / refused / unknown from the same minimal "List of Companies"
  envelope `IsReachableAsync` already used, so it's one extra HTTP round
  trip per checkin cycle, not two.
- **`install.ps1`**: self-elevation re-invokes itself with
  `-NoProfile -ExecutionPolicy Bypass -File` plus every bound parameter
  re-quoted, so a zero-argument right-click still ends up zero-argument
  after the elevation hop. Placeholder detection checks for `""`,
  `REPLACE_ME`, and `__SHOP_API_KEY__` — the last two aren't emitted by
  `_generate_appsettings()` today (it always writes the real key when
  called), but are recognized defensively in case a future manual/support
  flow ships a template zip with one of those placeholders.

**Verification run 2026-09-09:** backend 452 passed / 2 skipped (up from 424
at F1a's own commit — +28 tests across `test_tally_agent_installer.py` (new),
`test_tally_self_serve.py` (new), and additions to `test_tally_connector.py` /
`test_tally_agent.py`); `alembic heads` confirms `0026` chains cleanly off
`0025`; `Base.metadata.create_all` matches the migration's columns; `dotnet
build TallyAgent.slnx` 0 warnings/0 errors; web `tsc --noEmit`, `eslint`,
`vite build` all clean. **No live e2e run** — that remains the single biggest
gap, same as F1a's own unresolved item, now stacked with "does a real shop PC
actually install and phone home from this zip".

**Not yet done, next session:**
1. Publish a real `dotnet publish -r win-x64 --self-contained` build to
   `tally_agent_build_dir` in every environment that needs to provision an
   agent (dev box included, to do the live e2e run at all).
2. Confirm/fix `settings.base_url` for prod in `fleek-infra` — push infra
   first if it needs to change.
3. Provision a real firm, download the real zip, run `install.ps1` on an
   actual Windows box (ideally the dev box's existing TallyPrime install),
   confirm the checkin flips green and — with "acts as Server" on —
   `tally_status` flips to `connected` within a minute.
4. Commit + deploy (`alembic upgrade head` runs `0026`).

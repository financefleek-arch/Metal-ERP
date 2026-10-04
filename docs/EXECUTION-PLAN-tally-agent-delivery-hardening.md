# EXECUTION PLAN — Tally Agent: build pipeline, auto-update, Windows hardening

Status: **BUILT 2026-10-03, not yet released / not committed.** Operational reference
is now `tally-agent/README.md` (run model, PC requirements, AV, release + update flow,
testing, troubleshooting). This doc records *why* and what changed from the first draft.
Follows `EXECUTION-PLAN-tally-agent-seamless-onboarding.md`.

Origin: a review of the installer + `fleek-infra` wiring found four delivery gaps
(G1-G4) and Windows-side assumptions (W1-W8) that had only been exercised on one dev PC.

## Decisions (locked 2026-10-03)

1. **Per-user Scheduled Task is the default; the Windows service is an opt-in
   (`-AsService`).** TallyPrime only serves its gateway while running in a logged-in
   session, so a service that starts at boot gains almost nothing, and LocalSystem cannot
   see mapped drives / user folders / the user's proxy. Per-user needs no UAC and looks
   far less like malware to AV. Keep the service for a Tally server / RDP host.
2. **Free signing for now, key in Vault KV.** No code-signing certificate until we onboard shops
   we don't hand-hold. Update integrity comes from our own **ECDSA P-256** signature over each
   release (the first draft said Ed25519 - .NET has no built-in Ed25519, P-256 is built in), not
   from Authenticode. **The private key is a Vault KV secret handled like every other Fleek
   secret** (`secret/metalerp/core#agent_signing_key`; `load-vault-secrets.sh` -> `metalerp-api`
   as `AGENT_SIGNING_KEY`). **Fleek's `.env` principle is followed strictly: no new `.env`
   variable** (`.env` = Vault bootstrap login only; non-secrets -> fleek `platform_secrets`; secrets
   -> Vault) and no new AppRole/policy change/engine - one new field in the existing `metalerp/core`
   secret (already covered by `infra-deploy-read-policy`), one `_lvs_export` line, one compose line. History: first
   draft was a GitHub secret; then a non-exportable Vault Transit key with a hand-run wrapper
   (prototyped and tested against a real Vault, then dropped) - both replaced 2026-10-03 to match
   Fleek's existing pattern and avoid new infra. **Accepted trade-off:** the key sits in the API
   container's environment and is readable by the infra-deploy AppRole, so compromise of either is
   compromise of signing (RUNBOOK covers rotation/response). What still gates a release: CI cannot
   sign; `sign` is a human step that re-hashes the zip and needs the hash typed back; shops accept
   only our key. *Buy an OV certificate when:* the first real AV quarantine at a shop, installs
   without us on the call, or ~10 shops. (Azure Artifact Signing is not available in India;
   cheapest India-usable OV is ~$116-220/yr; EV no longer gives instant SmartScreen trust.) The
   Sectigo DV TLS cert for fleekfinance.in cannot be used for code signing.
3. **Hand-hold the first customers, after testing on machines with AV** - see the test matrix
   in `tally-agent/README.md`.
4. **Auto-update is in.** Supersedes onboarding decision #7 ("No auto-update for the pilot").
5. **R2 is the release store** - a dedicated bucket `metaerp-tallyagent` (prefix `agent-releases/`;
   `TALLY_R2_RELEASE_BUCKET`, compose default, same shared credential - verified it can reach it),
   not an scp to the VPS. Dedicated because the GitHub CI token must not be able to touch customer
   backups or the per-shop installers (keys baked in) in the main bucket; those stay where they are. Releases are tagged then *promoted* separately; a shop can be pinned
   to a not-yet-promoted version first (canary).
6. **In-process updater, not an `update.ps1`.** The first draft had a PowerShell script swap
   the files. Dropped: a PowerShell child process launched by the agent is exactly the pattern
   AV heuristics flag. Windows lets a running exe be *renamed*, so the agent stages the build,
   exits, and its next start moves the old files aside and copies the new ones in; the
   scheduler / SCM restarts it. Proven on real Windows (see Verification).
7. **Platform floor: Windows 10 1607+ / 11 / Server 2016+, 64-bit** (self-contained .NET 10).
   Windows 7 / 8.1 and 32-bit are out of scope; the installer's pre-flight says so. Re-open only
   if a pilot shop is on one.
8. **LocalSystem vs dedicated service account:** per-user task by default; `-AsService` keeps
   LocalSystem (no `-ServiceAccount` option built - add if a NAS-backup shop needs it).

## Gaps and how each was closed

| Gap | Closed by |
|-----|-----------|
| **G1** no build pipeline | `.github/workflows/agent-release.yml` (tag `agent-vX.Y.Z`: test, publish win-x64 self-contained, `--selftest` smoke, package via `tally-agent/tools/release.py`, upload UNSIGNED to R2 with `pending.json`). Signing + promotion are on the VPS: `docker exec -it metalerp-api python -m tools.agent_release sign|promote X.Y.Z`. New `agent` job in `ci.yml` (Windows: build + unit tests). Backend builds installers from the promoted R2 release, falling back to `TALLY_AGENT_BUILD_DIR` for dev. |
| **G2** no version awareness / auto-update | Agent reports `agent_version` + `os_version`; backend offers `update` (presigned URL + sha256 + size + signature) on checkin; per-shop pin `target_agent_version`; agent verifies signature -> downloads -> checks size/sha256 -> extracts -> runs the new exe's `--selftest` -> swaps at next start -> rolls itself back if it crash-loops; failure reported on the next checkin and not re-offered. Migration `0037`. |
| **G3** stale cached zip | A stale zip installs an old agent that **self-updates on its first checkin**; no key needed (updates never touch `appsettings.json`). `backup_shop.installer_agent_version` records what the zip carries; Ops console shows it. "Rotate key" is still the only way to regenerate a zip (the plaintext key exists only at provision/rotate). |
| **G4** unsigned / no integrity check | Releases signed with a Vault-held ECDSA P-256 key (never in GitHub or `.env`); the signer re-hashes the zip and needs a human confirmation; agent verifies signature + size + sha256 and **fails closed** until a public key is compiled in. Authenticode deferred (decision 2). |

| Windows item | Closed by |
|---|---|
| **W1** OS support | Pre-flight FAILs on < Win10 1607 / 32-bit; WARNs on ARM64. Floor documented (decision 7). |
| **W2** LocalSystem can't see user drives | Default is now per-user (runs as the user). `-AsService` pre-flight WARNs on a mapped-drive backup folder. |
| **W3** bare service lifecycle | `-AsService`: `sc failure` restart actions + `failureflag` + delayed-auto. Task mode: restart-on-failure + a 5-minute "start if not running" trigger. |
| **W4** key readable by all local users | Task mode: lives under the user's own profile. Service mode: `icacls` restricts `appsettings.json` to SYSTEM + Administrators. (DPAPI considered and dropped: it protects only against other users, which profile ACLs already do, and an admin password reset can orphan DPAPI data.) |
| **W5** no pre-flight | `install.ps1` pre-flight (OS, arch, PowerShell, disk, older service, reach Fleek, clock skew, Tally gateway, backup folder) with PASS/WARN/FAIL; `-PreflightOnly`; post-install waits up to 90 s and reports whether the agent actually checked in. |
| **W6** mark-of-the-web | `Unblock-File` over the source dir before install. |
| **W7** gateway host | Default `http://127.0.0.1:9000` (not `localhost`, which can resolve to `::1`); `-TallyGatewayUrl` for a Tally on another PC. |
| **W8** proxy / sleep / AV | README documents proxy (per-user uses the user's; service uses `netsh winhttp`), sleep, and AV handling + test matrix. Update rollback covers an AV quarantine of a new build. |
| `uninstall.ps1` | Added (task, Startup entry, service, files; `-Purge` for logs/state). |

## What was built

* **Backend:** migration `0037` (`backup_shop`: `agent_version`, `agent_version_at`,
  `os_version`, `target_agent_version`, `installer_agent_version`, `last_update_status`);
  `services/tally/agent_release.py` (release store, offers, pin logic); checkin accepts the new
  fields and returns `update`; installer builder reads the promoted release;
  `backup_storage.presigned_get_url` + `get_object(max_bytes=)`; admin `PUT
  /api/admin/firms/{id}/tally-shop/target-version` + version fields on the firm status; generated
  `appsettings.json` now uses `%LOCALAPPDATA%` paths and `127.0.0.1`. Tests:
  `test_tally_agent_updates.py` (+ updated installer / agent tests) - 38 pass in the touched files.
* **Agent:** `Updates/` (AgentVersion, ReleaseVerifier + ReleaseKeys, UpdateSwapper,
  UpdateCoordinator), `--selftest`, single-instance mutex, env-var path expansion, windowless
  Release build, version via `-p:Version`. `TallyAgent.Tests` (31 xunit tests).
* **Scripts/CI/docs:** `install.ps1` rewritten, `uninstall.ps1`, `tools/release.py`, `tools/e2e/`
  harness, `agent-release.yml`, `tally-agent/README.md`.
* **Signing:** `api/tools/agent_release.py` (`sign` / `promote` / `gen-key` / `pubkey` /
  `public-keys` / `status`; key from `AGENT_SIGNING_KEY`; `backup_storage.object_exists`);
  infra (local edits, not pushed): `load-vault-secrets.sh` `_lvs_export METALERP_AGENT_SIGNING_KEY`,
  `docker-compose.yml` `AGENT_SIGNING_KEY` on `metalerp-api`, and a rewritten RUNBOOK section
  "Agent release signing key" (ordered setup, rotation via `agent_signing_key_next`, loss/exposure).
* **Ops console:** "Agent version" block on the Tally Agent card (running / latest / installer
  versions, OS, rolled-back warning, pin + unpin).

## Verification

* Backend: full suite passes (~580, 2 pre-existing skips) on SQLite.
* Signer: `test_agent_release_tool.py` (21) - key parsing (one-line base64 / PEM / wrong curve),
  sign/verify, `gen-key` keeps the private key off stderr, refuses a changed / overwritten /
  config-carrying zip, hash-prefix confirmation, promote. The signer -> agent wire format is pinned
  by a fixture the .NET verifier must accept. (An earlier Transit design was exercised against a
  real Vault 1.17.6 dev server before being dropped; that proved the P-256 R||S format end to end.)
* Agent: 32 unit tests pass, including a real run of the built exe's `--selftest` (which, during
  development, correctly caught a build staged without its native sqlite DLL).
* **Update e2e on real Windows 11** (fake backend + real self-contained signed builds + a restart
  loop standing in for Task Scheduler): *(A)* 1.0.0 offered 1.1.0 -> downloaded -> exited -> swapped
  (running exe + loaded DLLs renamed successfully) -> restarted -> reported 1.1.0 -> declared
  healthy at 90 s -> old build + staging deleted, `appsettings.json` untouched. *(B)* a build that
  exits right after startup, offered as 1.2.0 -> swapped in -> crashed 4x -> restored 1.1.0 itself
  -> next checkin reported `update_failed_version=1.2.0` -> not offered again.
* Pre-flight run on this dev box: correctly PASSed 8 checks and FAILed on the already-installed
  service.
* **Not verified:** the VPS wiring (`load-vault-secrets.sh` fetching the new field, compose passing
  `AGENT_SIGNING_KEY` into `metalerp-api`, `gen-key` -> `vault kv patch` over `docker exec`); a real Task Scheduler restart (the harness emulates it - the installed task's
  restart-on-failure + 5-minute tick are untested live); a real install on a clean PC; any
  third-party AV; R2 upload from CI (needs secrets); the service-mode update path.

## Live result 2026-10-03

Release **1.0.0** went end to end in production: tag `agent-v1.0.0` -> CI (environment `agent-release`)
-> unsigned upload to bucket `metaerp-tallyagent` -> `sign` on the VPS (hash + commit checked against
the CI summary) -> `promote` -> pilot firm key rotated, installer rebuilt from the release
("installer carries 1.0.0"), installed with the new per-user installer. Ops console shows: running
1.0.0, agent online, Tally connected, cloud backup up to date. **Still unproven live:** an actual
auto-update (1.0.0 -> 1.0.1 canary via pin), rollback on a real machine, Task Scheduler restart across
logoff/reboot, third-party AV, a clean-PC install.

## Follow-up 2026-10-04: jobs that Tally never answers

Found by replaying "agent stopped / Tally disconnected midway" against the real backend: a Tally job is
delivered to the agent once, the agent kept it only in memory and tried once, so a job that met a closed
Tally was never resumed, and a stock-item push then sat `sent` forever and every new push was refused
("A push to Tally is already running"). Fixed: the agent holds and retries "not ready" jobs for ~8 min
(only when the request never reached Tally - never after a timeout / HTTP error), the backend cancels
stalled jobs at 10 min (vouchers and stock items regardless of a ping; pulls after 30 min unless "not
ready"), releases rows and withdraws the queued outbox item, and the dialog now says what is really
happening ("Waiting for TallyPrime...", batch X of N). See README "What happens when Tally (or the agent)
goes away mid-job". Tests: backend (`test_tally_items`, `test_tally_connector`), agent
(`TallyMastersRetryTests`). Not verified live: an actual Tally close/reopen against a real shop.

## Live result 2026-10-04: auto-update proven on a real machine

Release **1.0.1** (the Tally-retry fix) was tagged, CI-built, signed, then reached the pilot PC by itself:
download 21:11:40 -> staged + exit 21:11:50 -> scheduler restart 21:14:16 -> old build swapped the files
and exited -> scheduler restart 21:15:03 on 1.0.1 (about 3.5 min, nobody at the shop). Ops console then
showed Running 1.0.1, latest released 1.0.1, online, Tally connected. This also proves the real Task
Scheduler restart (previously only emulated). Observation: restart-on-failure does not reliably fire for
exit code 10, so each restart can wait for the 5-minute repeat tick (an update can take up to ~10 min).
Possible polish: 1-minute repeat tick in install.ps1; do the swap in the staging process so there is one
restart not two (only helps from the next release on). Still unproven live: rollback of a bad release,
restart after a PC reboot, third-party AV, clean-PC install.

## Left for you (nothing here can be done from the repo)

**Progress 2026-10-03:** signing key created in Vault (`secret/metalerp/core#agent_signing_key`, 184
chars, verified identical to the interim copy) and its public key is **compiled into
`ReleaseKeys`** (`ReleaseVerifier.cs`; guarded by a test that the list is non-empty and valid P-256).
Still open below: push `fleek-infra` + recreate `metalerp-api` + confirm `public-keys` matches; the
interim `secret/metalerp/agent-release` copy can be deleted; GitHub R2 secrets; tag/sign/promote;
installer-first rollout; pilot re-install; AV matrix.

1. **Create the signing key and wire it** - strictly in the RUNBOOK's order (a missing Vault field
   aborts every deploy): deploy Metal ERP; `gen-key` -> `vault kv patch secret/metalerp/core
   agent_signing_key=-` (patch, never put; no policy change); push `fleek-infra`; recreate
   `metalerp-api`; confirm `public-keys` matches; paste that public key into `ReleaseKeys` and
   commit. Until a build with the key is installed every agent refuses updates - so the first
   build goes to shops by installer.
2. **GitHub Environment `agent-release`** (done 2026-10-03; set required reviewer + `agent-v*` tags
   only) with R2 secrets for the new bucket only: `AGENT_R2_ENDPOINT_URL`,
   `AGENT_R2_ACCESS_KEY_ID`, `AGENT_R2_SECRET_ACCESS_KEY`, `AGENT_R2_BUCKET`.
3. Tag `agent-v1.0.0`; on the VPS `docker exec -it metalerp-api python -m tools.agent_release sign
   1.0.0` (compare the hash with the CI run summary), then `... promote 1.0.0`.
4. **Re-install the pilot box** (it runs the old service build with no update support): run
   `uninstall.ps1` as Administrator, then the new `install.ps1`.
5. **Run the AV / clean-machine matrix** (README) before the first non-friendly shop.
6. Push `fleek-infra` (`load-vault-secrets.sh`, `docker-compose.yml`, RUNBOOK) - local edits only so
   far, not committed or pushed - but only AFTER the Vault field exists (step 1).
7. Optional infra tidy, *after* the R2 flow is proven: drop the `tally-agent-build` bind mount from
   `fleek-infra`'s `metalerp-api` service (push `fleek-infra` first, per infra-alignment). Harmless
   to leave - the backend only uses the directory as a fallback.
8. Apply migration `0037` (auto-runs on deploy). It chains after `0036` (catalog), which is part of
   the parallel, uncommitted catalog work - commit order matters.

## Known limits / follow-ups

* A compromised backend could offer an older *signed* version (downgrade). Mitigation is process:
  don't leave releases with known holes in the bucket.
* Update is pull-only on the 60 s checkin; there is no push/email on `update_failed` (dashboard-only,
  consistent with the offline-alert decision).
* No `-ServiceAccount` option (a shop whose backups sit on a NAS only reachable by a specific
  account while running in service mode).
* Agent modules still have no tests; only the update path does.

# Tally Agent

The Windows companion program that runs on a shop's PC next to TallyPrime. It
(1) pulls Tally masters, (2) pushes sales/purchase vouchers into Tally, (3) uploads
Tally's own backup files to the cloud, and (4) reports health to Metal ERP. It talks
to TallyPrime's local HTTP gateway and to the Metal ERP backend over HTTPS - it
never exposes a port.

This page is the operational reference: what the agent expects of the PC, how it is
installed, how releases and auto-update work, how it behaves with antivirus, and how
to test it. Design history: `docs/EXECUTION-PLAN-tally-agent-seamless-onboarding.md`
and `docs/EXECUTION-PLAN-tally-agent-delivery-hardening.md`.

## Run model: per-user task (default) vs Windows service

| | **Per-user task** (default) | Windows service (`-AsService`) |
|---|---|---|
| Needs Administrator | No | Yes |
| Installed to | `%LOCALAPPDATA%\TallyAgent\app` | `C:\Program Files\TallyAgent` |
| Data / logs | `%LOCALAPPDATA%\TallyAgent` | `C:\ProgramData\TallyAgent` |
| Runs as | the logged-in user (who also runs Tally) | LocalSystem |
| Starts | at that user's logon, plus a 5-minute "start if not running" tick | at boot (delayed) |
| Sees user's drives / proxy / OneDrive | **Yes** | No (no mapped drives, no user profile) |
| Use it when | a normal shop PC (almost always) | TallyPrime on a server / RDP host with nobody logged in |

Why per-user is the default: TallyPrime only serves its gateway while it is running in
a logged-in session, so a service that starts before login has nothing to talk to - and
a service as LocalSystem cannot see the user's backup folder if it is a mapped drive or
in their profile. Per-user also needs no UAC and looks far less like malware to
antivirus (no service registration, no Program Files write).

Only ONE agent runs per PC (`Global\TallyAgentSingleton` mutex). If a second copy starts
- a second RDP session, or a leftover service plus the new task - it exits silently.
`install.ps1` refuses to install over an old service; remove it first with `uninstall.ps1`.

## What the agent expects of the PC

* **Windows 10 1607 or newer / Windows 11 / Server 2016+**, **64-bit**. The build is a
  self-contained .NET 10 `win-x64` app (no .NET install needed). It will not run on
  Windows 7 / 8.1 or 32-bit Windows; `install.ps1`'s pre-flight says so. ARM64 PCs run
  it under x64 emulation (untested).
* **PowerShell 5.1** (built into Windows 10/11) for the installer only.
* ~500 MB free disk (the build is ~80 MB installed / ~38 MB zipped; an update briefly keeps two copies).
* Outbound **HTTPS** to the Metal ERP backend and to Cloudflare R2 (backup upload,
  update download). No inbound ports. A machine behind a proxy: the per-user task uses
  the user's normal proxy settings; the service uses the machine WinHTTP proxy
  (`netsh winhttp`).
* A correct **clock** (within a few minutes) - pre-signed upload URLs fail otherwise.
* **TallyPrime running with a company open** and F1 > Settings > Connectivity >
  "TallyPrime acts as Server" (port 9000) for masters pull and voucher push. If it
  isn't, the agent reports `Tally not reachable` and retries every minute; backups still
  upload.
* **TallyPrime on the same PC** by default (`http://127.0.0.1:9000`). If Tally runs on
  another PC on the LAN, install with `-TallyGatewayUrl http://<that-pc>:9000`, and allow
  inbound 9000 on the Tally PC.
* TallyPrime's **Data Backup** scheduled to a local folder (default `C:\Tally\Backup`),
  versioned backups on - see the README inside the shop's zip.
* **The PC must be on, and the user logged in** (task mode). A PC that sleeps or is shut
  at night sends nothing until it wakes; the Ops console shows the agent offline after
  5 minutes.

## Install / uninstall (what the shop does)

1. Ops console -> firm -> **Tally Agent** -> *Set up Tally sync* (once), then *Download
   installer*. The zip is built per firm with the key and backend URL baked in.
2. On the shop PC: extract the zip, right-click `install.ps1` -> *Run with PowerShell*.
3. The installer prints a **pre-flight** table (PASS / WARN / FAIL), installs, then waits up
   to 90 s and reports whether the agent **checked in with Fleek**.
4. In TallyPrime turn on "TallyPrime acts as Server".

`install.ps1 -PreflightOnly` checks the PC and changes nothing. Other switches:
`-AsService`, `-TallyGatewayUrl`, `-WatchFolder`, `-AddDefenderExclusion` (admin; only if
Defender quarantined the exe), `-SkipPreflight`.

`uninstall.ps1` removes the task / Startup entry / service and the program files; logs and
state are kept unless `-Purge`.

We **hand-hold the first shops**: run the installer with them on a call, and read the
pre-flight output together.

## Antivirus / SmartScreen - what to expect

The agent is currently **unsigned**. Expect, on a clean machine:

* the browser may say the zip is "not commonly downloaded" (Keep -> Keep anyway);
* `install.ps1` shows an "Open File - Security Warning" (the installer also clears the
  downloaded-from-internet mark on the files it is about to run);
* Windows Defender / third-party AV may **quarantine `TallyAgent.exe`**. Self-update (a
  program that downloads and replaces its own files) and uploading files to the cloud are
  behaviours heuristics watch.

What the design already does to reduce this: no service registration, no Administrator, no
Program Files write, no PowerShell child process at update time (the updater is in-process),
a stable install path, and byte-stable releases (reputation attaches to file hashes - don't
rebuild without changes).

What we do about it: submit every release to Microsoft's false-positive portal; if a shop's
AV quarantines it, restore + exclude `%LOCALAPPDATA%\TallyAgent` (or `-AddDefenderExclusion`).
**Trigger to buy an OV code-signing certificate:** the first real AV quarantine at a shop,
installs happening without us on the call, or ~10 shops.

### AV / clean-machine test matrix (to run before the first non-friendly shop)

Run on clean VMs / Windows Sandbox snapshots. For each: browser download -> extract ->
`install.ps1` -> confirm check-in in the Ops console -> pull masters -> push a voucher ->
publish a newer version and watch it auto-update -> force a bad build and watch the rollback.

| Environment | Download warning | Install OK | Exe quarantined? | Update OK | Notes |
|---|---|---|---|---|---|
| Windows 10 22H2 + Defender | | | | | |
| Windows 11 + Defender | | | | | |
| Windows Server 2019 + Defender | | | | | |
| Windows 10 + Quick Heal (trial) | | | | | |
| Windows 10 + K7 / Kaspersky (trial) | | | | | |
| + upload `TallyAgent.exe` to VirusTotal | n/a | n/a | _detections:_ | n/a | |

## Releases and auto-update

```
tag agent-vX.Y.Z  ->  CI: test, publish, selftest, package, upload UNSIGNED (invisible to shops)
VPS:  docker exec -it metalerp-api python -m tools.agent_release sign X.Y.Z
                  ->  you confirm the hash; it signs; release.json
Ops console:  pin ONE shop to X.Y.Z    ->  canary
VPS:  docker exec -it metalerp-api python -m tools.agent_release promote X.Y.Z
                  ->  latest.json  ->  everyone updates
```

* **Store:** a dedicated R2 bucket, `metaerp-tallyagent` (`TALLY_R2_RELEASE_BUCKET`, defaulted in
  compose; falls back to `TALLY_R2_BUCKET` if unset), prefix `agent-releases/`. It holds only generic,
  key-free build files, so the GitHub CI token is scoped to it and cannot reach customer backups or
  the per-shop installers (those stay in the main bucket, written only by the API). The existing
  `r2/shared` credential can read/write it (verified 2026-10-03), so no new Vault secret: `<ver>/tally-agent-<ver>.zip` (`publish/` + `install.ps1` +
  `uninstall.ps1`), `<ver>/pending.json` (written by CI, unsigned, **not visible to the API**),
  `<ver>/release.json` (written by the signer after you approve it), and `latest.json` (a copy of
  the promoted `release.json`). Releases are immutable; both CI and the signer refuse to overwrite.
* **Backend** (`api/app/services/tally/agent_release.py`): new installers are built from
  `latest.json`'s zip (falling back to `TALLY_AGENT_BUILD_DIR` for dev). At each 60 s checkin
  the agent reports `agent_version`; the backend replies with an `update` offer (presigned
  URL + sha256 + size + signature) when the shop's target is different. Target = the Ops pin
  if set, else `latest`. An unpinned shop only ever moves **forward**; a pin may move either
  way (that's the rollback lever). An agent that never reported a version is never offered
  an update. A version the agent reported as failed is not offered again.
* **Trust:** every release is signed with an **ECDSA P-256 key kept in Vault KV**
  (`secret/metalerp/core#agent_signing_key`), handled like every other Fleek secret: fetched by
  `load-vault-secrets.sh`, passed to `metalerp-api` as `AGENT_SIGNING_KEY`, **nothing in `.env`**.
  CI holds no signing key. Signing (`api/tools/agent_release.py`, run with `docker exec` on the
  VPS) is a deliberate human step: it **re-hashes the zip itself** (not trusting CI's
  `pending.json`), refuses a zip carrying config, shows you the hash + git commit, makes you type
  the start of the hash, signs `"<version>|<sha256>|<size>"`, and only then writes `release.json`.
  The agent verifies the signature against public keys compiled into it
  (`ReleaseKeys.PublicKeysSpkiBase64`), then the downloaded zip's size and sha256. Net effect:
  getting CI to upload a zip is not enough to reach shops, and a compromised bucket cannot either.
  **Trade-off to know:** the key is in the API container's environment, so a compromise of
  `metalerp-api` (or of the infra-deploy Vault login) is a compromise of release signing - see the
  RUNBOOK for what to do. **Until a public key is compiled in, the agent refuses every update (fail
  closed).** Setup + rotation: `fleek-infra/vault/RUNBOOK.md`, "Agent release signing key".
* **Applying an update** (`Updates/UpdateCoordinator.cs`, `UpdateSwapper.cs`), no helper
  process: stage (download + verify + extract + run the new exe once with `--selftest` to
  prove it starts on THIS machine) -> exit -> the scheduler restarts -> the old build's
  startup **moves its files aside** (Windows allows renaming a running exe) and copies the
  new ones in -> exit -> restart on the new build. `appsettings.json` (the shop key) is never
  touched, so an update needs no key.
* **Rollback:** the new build must stay up ~90 s to be declared healthy ("process stayed up",
  *not* "backend reachable" - an offline PC must not roll back a good build). If it restarts
  more than 4 times without getting there, it restores the previous build, and the next
  checkin reports `update_failed_version` + the reason (shown in the Ops console).
* **Idle:** updates are only applied between module rounds, never mid-upload or mid-push.
* **Known limit:** a signed *older* version can be offered by a compromised backend (a
  downgrade). Keep releases with known holes out of the bucket if that matters.

### One-time setup before the first release

Order matters: a missing Vault field makes `load-vault-secrets.sh` abort every deploy of every
service. Full commands: `fleek-infra/vault/RUNBOOK.md` -> "Agent release signing key".

1. Deploy Metal ERP (carries `tools/agent_release.py` into `metalerp-api`).
2. On the VPS: `gen-key`, handed to `vault kv patch secret/metalerp/core agent_signing_key=-`
   through a guarded snippet (the private key never hits a file or the terminal; the public key
   prints - copy it). It is a new field in the existing `metalerp/core` secret, so **no policy
   change** - and always `patch`, never `put` (put would wipe `jwt_secret`).
3. Push `fleek-infra` (one `_lvs_export` line + one compose line), recreate `metalerp-api`, and
   check `python -m tools.agent_release public-keys` prints the same public key.
4. Paste that public key into `ReleaseKeys.PublicKeysSpkiBase64`
   (`TallyAgent/Updates/ReleaseVerifier.cs`) and commit.
5. GitHub **Environment** `agent-release` (required reviewer = you; tags `agent-v*` only) holding
   `AGENT_R2_ENDPOINT_URL`, `AGENT_R2_ACCESS_KEY_ID`, `AGENT_R2_SECRET_ACCESS_KEY`, `AGENT_R2_BUCKET`
   (`metaerp-tallyagent`) - an R2 token scoped to that bucket ONLY; no signing key.
6. Tag `agent-v1.0.0`; when CI finishes, on the VPS `sign 1.0.0` (compare the hash with the run
   summary), then `promote 1.0.0`.
7. **Ship the first build to shops by installer, not by update** - it is the first one that
   contains the public key, and an agent without the key refuses all updates.
8. The pilot box still runs the **old service build** (no update support): re-install it with
   the new per-user installer (run `uninstall.ps1` first).

Key rotation (ordered so shops never trust less than they need): see the RUNBOOK. In short -
stage the new key in `agent_signing_key_next`, ship a build trusting BOTH public keys signed with the
current key, wait for every shop to be on it, then activate the new key.

## Testing

* **Unit tests:** `dotnet test tally-agent/TallyAgent.slnx` - signature verification (tamper /
  wrong key / two keys), the file swap and rollback logic against temp folders, zip-slip,
  update staging (bad signature, hash mismatch, network failure, a build that fails its
  selftest, and a real run of the built exe's `--selftest`). Runs in CI on Windows.
* **Backend tests:** `api/tests/test_tally_agent_updates.py` (offers, pin, failure marker,
  manifest parsing, installer sourcing) and `api/tests/test_agent_release_tool.py` (the signer:
  refuses a changed/overwritten/config-carrying zip, hash-prefix confirmation, signature
  verifies, promote) - part of the normal `pytest` run.
* **Signer wire format:** `TallyAgent.Tests/Fixtures/signer-signature.json` is a signature made by
  `api/tools/agent_release.py` (the production signer, throwaway key); the agent's verifier must
  accept it - pins the signer -> agent format, not just a .NET round trip.
* **Pre-flight on a real PC:** `install.ps1 -PreflightOnly -SourceDir <a folder with an
  appsettings.json>` changes nothing.
* **Update end-to-end test** (`tools/e2e/`, Windows, ~12 min): builds three signed
  self-contained releases from the repo source, runs a fake backend, and runs the real agent
  under a restart loop that stands in for Task Scheduler.
  1. `python tools/e2e/build_releases.py` (needs `pip install cryptography`)
  2. `echo 1.1.0 > tools/e2e/work/target.txt`; `python tools/e2e/fake_backend.py` (background)
  3. `powershell tools/e2e/run_agent_loop.ps1 -Minutes 14` (background)
  4. Watch `tools/e2e/work/events.log`: `1.0.0` is offered `1.1.0`, restarts twice, reports
     `1.1.0`. Then `echo 1.2.0 > tools/e2e/work/target.txt`: `1.2.0` is swapped in, crashes 4
     times, is rolled back, and `1.1.0` reports `failed=1.2.0`.
  It uses `TALLYAGENT_INSTANCE` so it runs beside a real installed agent. Last run: both
  scenarios passed on Windows 11 (2026-10-03).

## Troubleshooting

* **Logs:** `%LOCALAPPDATA%\TallyAgent\logs` (service: `C:\ProgramData\TallyAgent\logs`);
  `install-transcript.log` is the installer's own record.
* **Agent offline in the Ops console:** is the PC on and the user logged in? Is the task
  there (`Get-ScheduledTask TallyAgent`)? Newest log: `checkin rejected: 401` = the key is
  stale (rotate + re-download); `could not reach` = network / proxy / firewall.
* **Check-in OK but "Tally not reachable":** TallyPrime closed / no company open / "acts as
  Server" off / Tally on another PC (`-TallyGatewayUrl`).
* **Nothing starts, no log:** antivirus probably quarantined `TallyAgent.exe` - restore it and
  exclude the folder.
* **Update failed:** the Ops console shows `failed:<ver>`; the reason is in the shop's last
  error and the agent log (`Update` entries). Fix, release a newer version (or re-pin).
* **`.prev` / `.bad` folders beside `app`:** leftovers of an update; the agent deletes them
  on its next healthy start.

## Layout

```
TallyAgent/            the worker (net10.0). Modules/ = backup, backup health, WhatsApp, Tally masters+push
  Updates/             version, release-signature check, file swap/rollback, update lifecycle
TallyAgent.Tests/      xunit
install.ps1            installer (pre-flight, per-user task or -AsService)
uninstall.ps1
tools/release.py       package a release + pending.json (CI; signing is NOT done here)
tools/e2e/             update end-to-end harness
```

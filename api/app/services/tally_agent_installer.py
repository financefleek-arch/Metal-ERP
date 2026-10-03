"""Builds the per-shop tally-agent installer zip on the fly.

One pre-published Windows build is reused for every shop - only a small
generated `appsettings.json` differs per download, so nothing is compiled per
request. The build comes from, in order:

1. the promoted release in R2 (`agent-releases/latest.json`, uploaded by the
   `agent-release` CI workflow - see services/tally/agent_release.py); its zip
   already carries `publish/` + `install.ps1` + `uninstall.ps1`;
2. `settings.tally_agent_build_dir` (a hand-dropped `dotnet publish` output -
   the dev / pre-CI path), with the scripts taken from `tally-agent/` or from
   inside that directory.

A zip is a snapshot: the agent self-updates on its first checkin, so a stale
zip is harmless (see the agent's update flow).
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.tally import agent_release

_settings = get_settings()
log = logging.getLogger(__name__)

_README_TEMPLATE = """\
Tally Agent — install instructions
===================================

What this is: a small background program that lets Fleek read your TallyPrime
masters, send invoices into Tally, and keep copies of your Tally backups safe.
It runs under YOUR Windows login (no administrator rights, no Windows Service)
and starts by itself whenever you log in.

Install (about 2 minutes)
-------------------------
1. Extract this zip somewhere on the shop PC (e.g. Desktop). Do not run
   anything from inside the zip window.
2. Right-click install.ps1 and choose "Run with PowerShell".
   - A security prompt may appear ("Do you want to run...") - choose Open/Run.
   - The installer first checks the PC and prints a PASS / WARN / FAIL list.
     A WARN is information, not a problem. A FAIL says exactly what to fix.
3. In TallyPrime, open your company and turn ON:
       F1 -> Settings -> Connectivity -> "TallyPrime acts as Server"
   (port 9000). Leave TallyPrime open while you work.

That's it - the shop's key and address are already filled in; nothing to type.
The agent reports to Fleek within about a minute. It updates itself when Fleek
releases a new version.

If Windows or your antivirus warns about this program
-----------------------------------------------------
The program is new and not yet widely downloaded, so Windows SmartScreen
or an antivirus may say "unrecognised app" or "unknown publisher". That is
expected for now - choose "More info" -> "Run anyway" / "Keep", or tell us and
we will help you over a call. If an antivirus quarantines TallyAgent.exe,
restore it and add this folder to the antivirus' exclusions:
    %LOCALAPPDATA%\\TallyAgent

Things to know
--------------
- The agent works only while the PC is on and you are logged in. If the PC
  sleeps or is shut down overnight, nothing is sent until it wakes.
- TallyPrime must be running (with a company open) for invoices to reach Tally.
- If TallyPrime runs on a DIFFERENT PC on your network, tell Fleek - the agent
  needs to be pointed at that PC.
- Logs (if something looks wrong): %LOCALAPPDATA%\\TallyAgent\\logs
- To remove the agent: right-click uninstall.ps1 -> Run with PowerShell.


Configure TallyPrime's backup (one-time, do this once)
-----------------------------------------------------
The agent uploads TallyPrime's OWN backup files from a local folder — it
does not read the live company. Set TallyPrime up like this:

1. In TallyPrime: Gateway of Tally -> press Alt+Y -> "Backup" (or set up
   a scheduled backup under F1: Help -> Settings on newer builds).
2. For Destination, choose "Specify Path" (a LOCAL folder), NOT
   "TallyDrive (Cloud Storage)". Set the path to:
       {watch_folder}
   (or tell Fleek the folder you use, so we point the agent at it.)
3. Use the normal Data Backup. Each run writes a small manifest
   (TBK...900) and one or more data files (TDBK...001, .002 ... for a
   large company) — the agent picks up the whole set. If your setup also
   writes an ODBC/SQL export (TSDBK...), that's fine — the agent skips it.
   TallyPrime's own Restore does NOT clearly separate the two in its
   list, so we only ever ship the real Data Backup to you.
4. Turn ON versioned / numbered backups (so each run keeps a new copy
   instead of overwriting the last one). Without this the agent only ever
   sees a single, ever-changing set.
5. Schedule it to run at least daily (end of day is typical).

Once that's running, the agent uploads each new backup set and keeps the
most recent sets in the cloud. To recover, download EVERY file of a set
from the Fleek app into one folder, then use TallyPrime's Restore
(Alt+F3 -> Data -> Restore) pointed at that folder.
"""


class BuildNotAvailable(Exception):
    """`tally_agent_build_dir` is missing or empty — the build hasn't been
    published/dropped there yet."""


@dataclass(frozen=True)
class BuiltInstaller:
    zip_bytes: bytes
    agent_version: str | None  # None = hand-dropped dir build, version unknown


# Files the per-shop zip supplies itself (or must never ship).
_SKIP_NAMES = {"appsettings.json", "appsettings.development.json"}
_SCRIPTS = ("install.ps1", "uninstall.ps1")


def build_installer_zip(
    *, shop_api_key: str, backend_base_url: str, watch_folder: str = "C:\\Tally\\Backup"
) -> bytes:
    return build_installer(
        shop_api_key=shop_api_key, backend_base_url=backend_base_url, watch_folder=watch_folder
    ).zip_bytes


def build_installer(
    *, shop_api_key: str, backend_base_url: str, watch_folder: str = "C:\\Tally\\Backup"
) -> BuiltInstaller:
    appsettings = _generate_appsettings(
        shop_api_key=shop_api_key, backend_base_url=backend_base_url, watch_folder=watch_folder
    )
    readme = _README_TEMPLATE.replace("{watch_folder}", watch_folder)

    release = _promoted_release_or_none()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        version: str | None
        if release is not None:
            blob = agent_release.release_zip_bytes(release)
            with zipfile.ZipFile(io.BytesIO(blob)) as src:
                for info in src.infolist():
                    if info.is_dir() or Path(info.filename).name.lower() in _SKIP_NAMES:
                        continue
                    zf.writestr(info.filename, src.read(info))
            version = release.version
        else:
            _add_build_dir(zf)
            version = None
        zf.writestr("publish/appsettings.json", appsettings)
        zf.writestr("README.txt", readme)
    return BuiltInstaller(zip_bytes=buf.getvalue(), agent_version=version)


def _promoted_release_or_none() -> agent_release.Release | None:
    """The CI-published release, or None to fall back to the build dir. Any
    release-store problem falls back rather than failing a provision."""
    try:
        return agent_release.get_latest_release()
    except Exception:  # noqa: BLE001
        log.warning("agent release store unavailable; using build dir", exc_info=True)
        return None


def _add_build_dir(zf: zipfile.ZipFile) -> None:
    build_dir = Path(_settings.tally_agent_build_dir)
    if not build_dir.is_dir() or not any(build_dir.iterdir()):
        raise BuildNotAvailable(
            f"No tally-agent build found at {build_dir} and no promoted release in R2 - "
            "publish one (tag agent-vX.Y.Z, then promote it in the agent-release workflow)"
        )
    # The scripts may also live inside build_dir (prod layout) - they are
    # written once at the zip's top level below, so skip a second copy under
    # publish/.
    skip = _SKIP_NAMES | set(_SCRIPTS)
    for path in build_dir.rglob("*"):
        if path.is_file() and path.name.lower() not in skip:
            zf.write(path, arcname=f"publish/{path.relative_to(build_dir)}")
    zf.writestr("install.ps1", _script_path("install.ps1").read_text(encoding="utf-8-sig"))
    try:  # optional: an older prod build dir may only have install.ps1
        zf.writestr("uninstall.ps1", _script_path("uninstall.ps1").read_text(encoding="utf-8-sig"))
    except BuildNotAvailable:
        log.warning("uninstall.ps1 not found - shipping the installer without it")


def _script_path(name: str) -> Path:
    """Locate an agent script (install.ps1 / uninstall.ps1) for the build-dir
    path. Two layouts:

    1. **Dev monorepo** - `tally-agent/` is a sibling of `api/` (this file is
       `api/app/services/...`, so four `parents` up is the repo root).
    2. **Prod container** - the Docker build context is `api/` alone, so
       `tally-agent/` isn't in the image; the script must sit INSIDE
       `settings.tally_agent_build_dir` (the one bind-mounted directory).

    Read per request, not embedded, so editing a script needs no code change.
    The CI release zip makes layout 2 unnecessary (scripts travel in the zip).
    """
    dev_candidate = Path(__file__).resolve().parents[3] / "tally-agent" / name
    if dev_candidate.is_file():
        return dev_candidate

    prod_candidate = Path(_settings.tally_agent_build_dir) / name
    if prod_candidate.is_file():
        return prod_candidate

    raise BuildNotAvailable(
        f"{name} not found at {dev_candidate} or {prod_candidate} - "
        "drop it directly inside the published build directory "
        f"({_settings.tally_agent_build_dir})"
    )


def _generate_appsettings(*, shop_api_key: str, backend_base_url: str, watch_folder: str) -> str:
    data = {
        "Logging": {
            "LogLevel": {"Default": "Information", "Microsoft.Hosting.Lifetime": "Information"}
        },
        "Agent": {
            "ShopApiKey": shop_api_key,
            "BackendBaseUrl": backend_base_url,
            # Per-user install (default): the agent expands env vars. install.ps1 rewrites
            # these to C:\\ProgramData\\TallyAgent for the optional -AsService mode.
            "StateDbPath": "%LOCALAPPDATA%\\TallyAgent\\state.db",
            "LogDirectory": "%LOCALAPPDATA%\\TallyAgent\\logs",
            "BackupSync": {
                "Enabled": True,
                "WatchFolder": watch_folder,
                # One TallyPrime native backup run = a TBK…900 manifest plus
                # one or more TDBK…001/.002 data parts. Both globs, so a
                # split (large) backup is captured whole. Deliberately
                # excludes the ODBC/SQL export (TSDBK*), which TallyPrime
                # cannot restore from and which its Restore list doesn't
                # visibly distinguish — so we never ship it to the firm.
                "FilePatterns": ["TDBK*", "TBK*.900"],
                "PollIntervalMinutes": 5,
                "LocalRetentionCount": 7,
            },
            "BackupHealthMonitor": {
                "Enabled": True,
                "PollIntervalMinutes": 15,
                "ExpectedIntervalHours": 26,
            },
            "WhatsAppDelivery": {
                "Enabled": False,
                "PollIntervalMinutes": 5,
                "TallyGatewayBaseUrl": "http://127.0.0.1:9000",
            },
            # F1a masters-in. Mirrors AgentOptions.TallyMastersOptions defaults
            # so the module is configured by the download, not a hand-edit.
            "TallyMasters": {
                "Enabled": True,
                "GatewayUrl": "http://127.0.0.1:9000",
                "ExportDir": "",
                "PollIntervalMinutes": 1,
            },
        },
    }
    return json.dumps(data, indent=2)

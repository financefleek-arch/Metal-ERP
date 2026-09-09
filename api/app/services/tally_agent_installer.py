"""Builds the per-shop tally-agent installer zip on the fly.

One pre-published Windows build (dotnet publish -r win-x64 --self-contained,
dropped at `settings.tally_agent_build_dir` by a manual/CI step) is reused
for every shop — only a small generated `appsettings.json` differs per
download, so nothing is compiled per request.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.config import get_settings

_settings = get_settings()

_README = """\
Tally Agent — install instructions
===================================

1. Extract this zip somewhere on the shop PC (e.g. Desktop).
2. Right-click install.ps1 and choose "Run with PowerShell".
   If Windows warns about running scripts, right-click PowerShell itself
   and choose "Run as administrator", then run:
       powershell -ExecutionPolicy Bypass -File .\\install.ps1
   from inside this extracted folder.
3. The installer needs to run as Administrator — it registers a Windows
   Service so backups sync automatically, even before anyone logs in.

That's it — the shop's API key and backend address are already filled in
below; you should not need to type anything.

Logs (if something looks wrong): C:\\ProgramData\\TallyAgent\\logs
"""


class BuildNotAvailable(Exception):
    """`tally_agent_build_dir` is missing or empty — the build hasn't been
    published/dropped there yet."""


def build_installer_zip(
    *, shop_api_key: str, backend_base_url: str, watch_folder: str = "C:\\Tally\\Backup"
) -> bytes:
    build_dir = Path(_settings.tally_agent_build_dir)
    if not build_dir.is_dir() or not any(build_dir.iterdir()):
        raise BuildNotAvailable(
            f"No tally-agent build found at {build_dir} — publish it there first "
            "(dotnet publish -r win-x64 --self-contained)"
        )

    install_script = _install_script_path().read_text(encoding="utf-8-sig")
    appsettings = _generate_appsettings(
        shop_api_key=shop_api_key, backend_base_url=backend_base_url, watch_folder=watch_folder
    )

    # The build dir carries its own template appsettings.json (and dev
    # variant) — skip both so the per-shop one written below is the only
    # copy in the zip, not a silently-shadowed duplicate entry. install.ps1
    # may also live inside build_dir now (the prod layout) — it's written
    # once at the zip's top level below, so skip a second copy under
    # publish/ too.
    _skip = {"appsettings.json", "appsettings.development.json", "install.ps1"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in build_dir.rglob("*"):
            if path.is_file() and path.name.lower() not in _skip:
                zf.write(path, arcname=f"publish/{path.relative_to(build_dir)}")
        zf.writestr("install.ps1", install_script)
        zf.writestr("publish/appsettings.json", appsettings)
        zf.writestr("README.txt", _README)

    return buf.getvalue()


def _install_script_path() -> Path:
    """Locate install.ps1. Two layouts are supported:

    1. **Dev monorepo** — this file lives at `api/app/services/…`, and
       `tally-agent/` is a sibling of `api/` four levels up
       (`app/services/tally_agent_installer.py` -> `services` -> `app` ->
       `api` -> repo root -> `tally-agent/`).
    2. **Prod container** — the Docker build context is `api/` alone
       (see fleek-infra's metalerp Dockerfile), so `tally-agent/` is never
       copied into the image; layout 1's path doesn't exist there. Instead
       `install.ps1` is expected to sit INSIDE `settings.tally_agent_build_dir`
       itself, alongside the published `.exe`/`.dll`s — that directory is
       the only one guaranteed to be bind-mounted into the container (its
       parent generally isn't; a single-directory `docker-compose.yml`
       mount doesn't expose anything above it), so this is the one prod
       location that's actually visible.

    Read once per request rather than embedding a copy in this module, so
    editing install.ps1 doesn't require a code change.
    """
    dev_candidate = Path(__file__).resolve().parents[3] / "tally-agent" / "install.ps1"
    if dev_candidate.is_file():
        return dev_candidate

    prod_candidate = Path(_settings.tally_agent_build_dir) / "install.ps1"
    if prod_candidate.is_file():
        return prod_candidate

    raise BuildNotAvailable(
        f"install.ps1 not found at {dev_candidate} or {prod_candidate} — "
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
            "StateDbPath": "C:\\ProgramData\\TallyAgent\\state.db",
            "LogDirectory": "C:\\ProgramData\\TallyAgent\\logs",
            "BackupSync": {
                "Enabled": True,
                "WatchFolder": watch_folder,
                "FilePattern": "*",
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
                "TallyGatewayBaseUrl": "http://localhost:9000",
            },
            # F1a masters-in. Mirrors AgentOptions.TallyMastersOptions defaults
            # so the module is configured by the download, not a hand-edit.
            "TallyMasters": {
                "Enabled": True,
                "GatewayUrl": "http://localhost:9000",
                "ExportDir": "",
                "PollIntervalMinutes": 1,
            },
        },
    }
    return json.dumps(data, indent=2)

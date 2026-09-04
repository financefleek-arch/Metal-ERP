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

    install_script = _install_script_path()
    appsettings = _generate_appsettings(
        shop_api_key=shop_api_key, backend_base_url=backend_base_url, watch_folder=watch_folder
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in build_dir.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=f"publish/{path.relative_to(build_dir)}")
        zf.writestr("install.ps1", install_script)
        zf.writestr("publish/appsettings.json", appsettings)
        zf.writestr("README.txt", _README)

    return buf.getvalue()


def _install_script_path() -> str:
    # tally-agent/install.ps1 lives two levels above api/, at the repo root's
    # tally-agent/ folder — read once per request rather than embedding a
    # copy in this module, so editing install.ps1 doesn't require a code change.
    candidate = Path(__file__).resolve().parents[3] / "tally-agent" / "install.ps1"
    if not candidate.is_file():
        raise BuildNotAvailable(f"install.ps1 not found at {candidate}")
    return candidate.read_text(encoding="utf-8-sig")


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
        },
    }
    return json.dumps(data, indent=2)

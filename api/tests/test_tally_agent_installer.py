"""Per-shop tally-agent installer: appsettings generation + zip assembly.

`build_installer_zip` reads `settings.tally_agent_build_dir` at call time via
the module-level `_settings` in `tally_agent_installer.py` — tests patch that
attribute directly rather than the env var, since `get_settings()` is
`lru_cache`d at import.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.services import tally_agent_installer as installer


def test_generate_appsettings_includes_tally_masters_block() -> None:
    raw = installer._generate_appsettings(
        shop_api_key="sk_test_123",
        backend_base_url="https://api.example.com",
        watch_folder="C:\\Tally\\Backup",
    )
    data = json.loads(raw)
    agent = data["Agent"]
    assert agent["ShopApiKey"] == "sk_test_123"
    assert agent["BackendBaseUrl"] == "https://api.example.com"
    # F1a's module must be configured by the download, not a hand-edit.
    assert agent["TallyMasters"] == {
        "Enabled": True,
        "GatewayUrl": "http://localhost:9000",
        "ExportDir": "",
        "PollIntervalMinutes": 1,
    }
    # Existing blocks untouched.
    assert agent["BackupSync"]["WatchFolder"] == "C:\\Tally\\Backup"


def test_build_installer_zip_raises_when_build_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_dir = tmp_path / "no-build-here"
    monkeypatch.setattr(installer._settings, "tally_agent_build_dir", str(empty_dir))
    with pytest.raises(installer.BuildNotAvailable):
        installer.build_installer_zip(
            shop_api_key="sk_test_123", backend_base_url="https://api.example.com"
        )

    empty_dir.mkdir()
    with pytest.raises(installer.BuildNotAvailable):
        installer.build_installer_zip(
            shop_api_key="sk_test_123", backend_base_url="https://api.example.com"
        )


def test_build_installer_zip_bundles_build_plus_baked_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_dir = tmp_path / "publish"
    build_dir.mkdir()
    (build_dir / "TallyAgent.exe").write_bytes(b"fake-exe-bytes")
    (build_dir / "TallyAgent.dll").write_bytes(b"fake-dll-bytes")
    monkeypatch.setattr(installer._settings, "tally_agent_build_dir", str(build_dir))

    blob = installer.build_installer_zip(
        shop_api_key="sk_live_abc", backend_base_url="https://api.example.com"
    )

    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert "install.ps1" in names
        assert "README.txt" in names
        assert "publish/TallyAgent.exe" in names
        assert "publish/appsettings.json" in names

        settings_data = json.loads(zf.read("publish/appsettings.json"))
        assert settings_data["Agent"]["ShopApiKey"] == "sk_live_abc"
        assert settings_data["Agent"]["TallyMasters"]["Enabled"] is True

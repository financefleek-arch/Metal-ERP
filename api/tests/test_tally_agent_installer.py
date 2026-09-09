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


def test_install_script_falls_back_to_prod_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the container-layout bug: in prod the Docker
    build context is `api/` alone (fleek-infra's metalerp Dockerfile), so
    `tally-agent/install.ps1` is never inside the image and the dev
    sibling-directory lookup (`parents[3] / "tally-agent" / "install.ps1"`)
    can't find it — `parents[3]` from `app/services/tally_agent_installer.py`
    lands on the container filesystem root, not a repo checkout. This test
    forces that lookup to miss (as it does for real in the container) by
    monkeypatching Module.__file__'s resolution point, and confirms
    install.ps1 is still found one level above `tally_agent_build_dir` —
    where a `dotnet publish` + install.ps1 are dropped together on the
    bind-mounted host path.
    """
    # Simulate "the dev-sibling directory doesn't exist" without relying on
    # this checkout's own layout (which always has a real tally-agent/ four
    # levels up, so it can't be used to prove the fallback branch works).
    fake_module_file = tmp_path / "container-root" / "app" / "services" / "tally_agent_installer.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.write_text("# stand-in for __file__\n")
    monkeypatch.setattr(installer, "__file__", str(fake_module_file))

    build_dir = tmp_path / "agent-build" / "publish"
    build_dir.mkdir(parents=True)
    (build_dir / "TallyAgent.exe").write_bytes(b"fake-exe-bytes")
    monkeypatch.setattr(installer._settings, "tally_agent_build_dir", str(build_dir))

    # No tally-agent/install.ps1 anywhere above the fake module path -> dev
    # lookup misses, exactly like the real container.
    with pytest.raises(installer.BuildNotAvailable):
        installer.build_installer_zip(
            shop_api_key="sk_test_123", backend_base_url="https://api.example.com"
        )

    # Drop install.ps1 alongside the build (one level up), as the prod
    # publish step is expected to.
    (build_dir.parent / "install.ps1").write_text("# fake install script\n")

    blob = installer.build_installer_zip(
        shop_api_key="sk_test_123", backend_base_url="https://api.example.com"
    )
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert "install.ps1" in zf.namelist()

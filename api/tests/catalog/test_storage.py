"""Catalog object storage: local backend, key safety, key layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.catalog.storage import (
    LocalStorage,
    get_storage,
    image_key,
    sha256_hex,
    source_key,
)


def test_roundtrip_overwrite_delete(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path)
    st.put("catalog/t/c/img/a.jpg", b"one", "image/jpeg")
    assert st.get("catalog/t/c/img/a.jpg") == b"one"
    st.put("catalog/t/c/img/a.jpg", b"two", "image/jpeg")  # overwrite is atomic
    assert st.get("catalog/t/c/img/a.jpg") == b"two"
    assert not list(tmp_path.rglob("*.tmp"))
    st.delete("catalog/t/c/img/a.jpg")
    st.delete("catalog/t/c/img/a.jpg")  # deleting a missing key is harmless
    with pytest.raises(FileNotFoundError):
        st.get("catalog/t/c/img/a.jpg")


def test_put_many(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path)
    st.put_many((f"catalog/t/c/img/{i}.jpg", bytes([i]), "image/jpeg") for i in range(20))
    assert all(st.get(f"catalog/t/c/img/{i}.jpg") == bytes([i]) for i in range(20))


@pytest.mark.parametrize(
    "bad",
    ["", "/abs/path", "../escape", "a/../../escape", "a\\b", "C:/win", "a/../b"],
)
def test_unsafe_keys_are_rejected(tmp_path: Path, bad: str) -> None:
    st = LocalStorage(tmp_path)
    with pytest.raises(ValueError):
        st.put(bad, b"x")
    with pytest.raises(ValueError):
        st.get(bad)


def test_nothing_is_written_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    st = LocalStorage(root)
    with pytest.raises(ValueError):
        st.put("../outside.txt", b"x")
    assert not (tmp_path / "outside.txt").exists()


def test_key_layout_is_tenant_and_catalog_scoped() -> None:
    sha = sha256_hex(b"photo")
    assert len(sha) == 64
    assert image_key("T1", "C1", sha, ".JPEG") == f"catalog/T1/C1/img/{sha}.jpeg"
    assert image_key("T1", "C1", sha, "") == f"catalog/T1/C1/img/{sha}.jpg"
    assert source_key("T1", "C1") == "catalog/T1/C1/source.pdf"
    # same bytes -> same key (content-addressed, so duplicates collapse)
    assert image_key("T1", "C1", sha256_hex(b"photo"), "jpg") == image_key("T1", "C1", sha, "jpg")


def test_get_storage_falls_back_to_local_without_r2() -> None:
    # The test environment has no TALLY_R2_* settings.
    assert isinstance(get_storage(), LocalStorage)


def test_production_without_r2_refuses_instead_of_using_a_local_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local folder is not a mounted volume in the container; writing there
    would silently lose photos on the next rebuild."""
    from fastapi import HTTPException

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(HTTPException) as exc:
        get_storage()
    assert exc.value.status_code == 503
    assert "not set up" in exc.value.detail


def test_upload_is_503_in_production_without_r2(
    catalog_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings
    from app.main import app
    from tests.catalog.conftest import SAMPLE_PDF

    client, h, _ = catalog_client
    monkeypatch.setattr(get_settings(), "app_env", "production")
    app.dependency_overrides.pop(get_storage, None)  # use the real dependency
    r = client.post(
        "/api/supplier-catalogs",
        headers=h,
        files={"file": ("g.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 503
    assert client.get("/api/supplier-catalogs", headers=h).json() == []  # nothing half-created

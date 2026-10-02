"""Object storage for catalog source PDFs and product photos.

Keys are content-addressed under `catalog/<tenant>/<catalog>/`. In prod this
is R2 (the same `TALLY_R2_*` bucket the backup feature uses, under the
`catalog/` prefix, so no new env var). With R2 unconfigured (dev, tests) it falls
back to a local directory (`CATALOG_LOCAL_DIR`).

`put_many` uploads in parallel: a 172-page catalog has 2,000+ photos and a
sequential R2 round trip per photo would blow the upload budget.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

import boto3
from botocore.client import Config as BotoConfig
from fastapi import HTTPException

from app.config import get_settings

_UPLOAD_WORKERS = 16


class CatalogStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str = ...) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def put_many(self, items: Iterable[tuple[str, bytes, str]]) -> None: ...

    def delete_many(self, keys: Iterable[str]) -> None: ...

    def get_many(self, keys: Iterable[str]) -> dict[str, bytes | None]: ...


def _check_key(key: str) -> None:
    # Keys are built by us, but a bad one must never escape the root.
    parts = key.split("/")
    if not key or key.startswith("/") or ".." in parts or "\\" in key or ":" in key:
        raise ValueError(f"invalid storage key: {key!r}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def image_key(tenant_id: str, catalog_id: str, sha256: str, ext: str) -> str:
    ext = ext.lower().lstrip(".") or "jpg"
    return f"catalog/{tenant_id}/{catalog_id}/img/{sha256}.{ext}"


def source_key(tenant_id: str, catalog_id: str) -> str:
    return f"catalog/{tenant_id}/{catalog_id}/source.pdf"


def _get_or_none(storage: CatalogStorage, key: str) -> bytes | None:
    """One object, or None when it is missing or unreadable (a lost photo must not sink
    a whole catalog)."""
    try:
        return storage.get(key)
    except Exception:  # noqa: BLE001
        return None


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._made: set[Path] = set()  # directories already created (saves a syscall per put)

    def _path(self, key: str) -> Path:
        _check_key(key)
        path = self.root / key
        if os.name == "nt":
            # Keys are long (tenant + catalog UUIDs + a 64-char hash); on Windows dev
            # machines that blows past MAX_PATH, so use the extended-length form.
            return Path("\\\\?\\" + os.path.abspath(path))
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        if path.parent not in self._made:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._made.add(path.parent)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: a reader never sees half a file

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def put_many(self, items: Iterable[tuple[str, bytes, str]]) -> None:
        for key, data, ctype in items:
            self.put(key, data, ctype)

    def delete_many(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.delete(key)

    def get_many(self, keys: Iterable[str]) -> dict[str, bytes | None]:
        return {k: _get_or_none(self, k) for k in dict.fromkeys(keys)}


class R2Storage:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.tally_r2_bucket
        # One shared client: boto3 clients are thread-safe.
        self._client = boto3.client(
            "s3",
            endpoint_url=s.tally_r2_endpoint_url,
            aws_access_key_id=s.tally_r2_access_key_id,
            aws_secret_access_key=s.tally_r2_secret_access_key,
            config=BotoConfig(signature_version="s3v4", max_pool_connections=_UPLOAD_WORKERS),
            region_name="auto",
        )

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        _check_key(key)
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        _check_key(key)
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = resp["Body"].read()
        return body

    def delete(self, key: str) -> None:
        _check_key(key)
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def put_many(self, items: Iterable[tuple[str, bytes, str]]) -> None:
        batch = list(items)
        with ThreadPoolExecutor(max_workers=_UPLOAD_WORKERS) as pool:
            # list() so the first failure raises here, not silently in a thread.
            list(pool.map(lambda it: self.put(it[0], it[1], it[2]), batch))

    def delete_many(self, keys: Iterable[str]) -> None:
        with ThreadPoolExecutor(max_workers=_UPLOAD_WORKERS) as pool:
            list(pool.map(self.delete, list(keys)))

    def get_many(self, keys: Iterable[str]) -> dict[str, bytes | None]:
        unique = list(dict.fromkeys(keys))
        with ThreadPoolExecutor(max_workers=_UPLOAD_WORKERS) as pool:
            return dict(zip(unique, pool.map(lambda k: _get_or_none(self, k), unique), strict=True))


def get_storage() -> CatalogStorage:
    """R2 when configured; the local directory only outside production. Used as a
    FastAPI dependency so tests can override it with a tmp-dir `LocalStorage`.
    """
    s = get_settings()
    if s.tally_r2_configured:
        return R2Storage()
    if s.is_production:
        # The local folder is not a mounted volume in the container: photos written
        # there would vanish on the next rebuild. Refuse instead of failing silently.
        raise HTTPException(
            status_code=503,
            detail="Photo storage is not set up on this server. Ask your administrator.",
        )
    return LocalStorage(s.catalog_local_dir)

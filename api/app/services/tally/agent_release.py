"""Agent release store (R2) + auto-update offers.

CI (`.github/workflows/agent-release.yml`) uploads each tagged agent build to
the agent-release R2 bucket (`settings.agent_release_bucket`, a dedicated bucket the
GitHub token is scoped to; falls back to the main tally bucket if unset), under
`agent-releases/`:

    agent-releases/<ver>/tally-agent-<ver>.zip   publish/ + install.ps1 + uninstall.ps1
    agent-releases/<ver>/release.json            manifest (below)
    agent-releases/latest.json                   copy of one release.json — the
                                                 "promoted" version; written only
                                                 by the workflow's promote action

`release.json`: {version, sha256, size_bytes, signature, zip_key, built_at}.
`signature` is an ECDSA P-256/SHA-256 signature, made in CI with a private key
this API never holds, over the UTF-8 string ``"<version>|<sha256>|<size_bytes>"``.
The API only relays it — the agent verifies it against a public key compiled
into the agent, so a compromised API/R2 still cannot push code to shops.

Update policy: a shop's target is its pinned `target_agent_version` if set
(canary / rollback lever), else the promoted `latest`. Nothing is offered to an
agent that hasn't reported a version (it predates auto-update), and a version
the agent already reported as failed is not re-offered.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app import backup_storage
from app.config import get_settings
from app.models import BackupShop

log = logging.getLogger(__name__)

RELEASE_PREFIX = "agent-releases"
LATEST_KEY = f"{RELEASE_PREFIX}/latest.json"
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_MAX_ZIP_BYTES = 512 * 1024 * 1024
_LATEST_TTL_SECONDS = 60

_latest_cache: tuple[float, Release | None] | None = None


@dataclass(frozen=True)
class Release:
    version: str
    sha256: str
    size_bytes: int
    signature: str
    zip_key: str


def _parse(raw: bytes) -> Release | None:
    try:
        d = json.loads(raw)
        rel = Release(
            version=str(d["version"]),
            sha256=str(d["sha256"]).lower(),
            size_bytes=int(d["size_bytes"]),
            signature=str(d["signature"]),
            zip_key=str(d["zip_key"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not _VERSION_RE.match(rel.version) or not rel.zip_key.startswith(f"{RELEASE_PREFIX}/"):
        return None
    return rel


def _read_manifest(key: str) -> Release | None:
    """None if R2 is unconfigured, the key is missing, or the manifest is bad —
    callers treat all three as 'no release available'."""
    if not get_settings().tally_r2_configured:
        return None
    try:
        return _parse(
            backup_storage.get_object(
                key, max_bytes=1024 * 1024, bucket=get_settings().agent_release_bucket
            )
        )
    except Exception as exc:  # noqa: BLE001 — NoSuchKey / network: just 'none'
        log.info("agent release manifest %s unavailable: %s", key, exc)
        return None


def get_latest_release() -> Release | None:
    """The promoted release. Cached briefly: every shop's 60s checkin asks."""
    global _latest_cache
    now = time.monotonic()
    if _latest_cache is not None and now - _latest_cache[0] < _LATEST_TTL_SECONDS:
        return _latest_cache[1]
    rel = _read_manifest(LATEST_KEY)
    _latest_cache = (now, rel)
    return rel


def get_release(version: str) -> Release | None:
    if not _VERSION_RE.match(version):
        return None
    return _read_manifest(f"{RELEASE_PREFIX}/{version}/release.json")


def clear_cache() -> None:
    global _latest_cache
    _latest_cache = None


def release_zip_bytes(release: Release) -> bytes:
    """The release zip, cached on local disk by version (a provision would
    otherwise re-download ~30 MB from R2 every time)."""
    cache = Path(tempfile.gettempdir()) / "agent-release-cache" / f"{release.version}.zip"
    if cache.is_file() and cache.stat().st_size == release.size_bytes:
        return cache.read_bytes()
    blob = backup_storage.get_object(
        release.zip_key, max_bytes=_MAX_ZIP_BYTES, bucket=get_settings().agent_release_bucket
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(blob)
    return blob


def version_tuple(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def effective_target(shop: BackupShop) -> Release | None:
    if shop.target_agent_version:
        return get_release(shop.target_agent_version)
    return get_latest_release()


def update_offer_for(shop: BackupShop) -> dict | None:
    """The `update` object for this shop's checkin response, or None."""
    current = shop.agent_version
    if not current or not _VERSION_RE.match(current):
        return None
    target = effective_target(shop)
    if target is None or target.version == current:
        return None
    # An unpinned shop only ever moves forward; a pin may move it either way
    # (that is the rollback lever).
    if not shop.target_agent_version and version_tuple(target.version) <= version_tuple(current):
        return None
    if shop.last_update_status == f"failed:{target.version}":
        return None
    return {
        "version": target.version,
        "url": backup_storage.presigned_get_url(
            target.zip_key, bucket=get_settings().agent_release_bucket
        ),
        "sha256": target.sha256,
        "size_bytes": target.size_bytes,
        "signature": target.signature,
    }

"""Build + cache the per-shop tally-agent installer zip.

The zip is assembled once, at the moment a shop's API key is minted
(provision) or rotated — the only two points the plaintext key exists. It's
stored in R2 (`installers/<shop_id>.zip`) and the download routes just stream
those bytes. Rotating the key overwrites the same deterministic key, so the
old download stops resolving to a valid agent identity even though the URL is
unchanged.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.backup_storage import put_object
from app.config import get_settings
from app.models import BackupShop
from app.services.tally_agent_installer import BuildNotAvailable, build_installer_zip

__all__ = ["BuildNotAvailable", "build_and_cache_installer", "installer_r2_key", "installer_filename"]


def installer_r2_key(shop: BackupShop) -> str:
    return f"installers/{shop.id}.zip"


def installer_filename(firm_name: str) -> str:
    slug = re.sub(r"[^\w]+", "-", firm_name, flags=re.UNICODE).strip("-")[:60] or "shop"
    return f"tally-agent-{slug}.zip"


def build_and_cache_installer(
    session: Session, shop: BackupShop, *, plaintext_key: str
) -> str:
    """Build the shop's installer zip and put it in R2. Returns the r2_key.

    Raises `BuildNotAvailable` if the published agent build hasn't been
    dropped at `settings.tally_agent_build_dir` yet — the caller should turn
    that into a 503; the shop's agent identity still persists and a later
    rotate-key will build the zip once the build lands.
    """
    zip_bytes = build_installer_zip(
        shop_api_key=plaintext_key,
        backend_base_url=get_settings().base_url,
    )
    key = installer_r2_key(shop)
    put_object(key, zip_bytes, "application/zip")
    shop.installer_r2_key = key
    session.flush()
    return key

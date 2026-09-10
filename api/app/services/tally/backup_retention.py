"""Cloud retention for the companion agent's backup uploads.

Keep at most N confirmed backups per shop; after each new confirm, prune
the oldest (R2 object + `BackupUpload` row). N is
`BackupShop.backup_retention_count` when set, else the global
`settings.tally_backup_retention_count`.

Only `confirmed` rows are pruned — `pending`/`failed` rows carry no real
storage and a `failed` row may still retry. Best-effort: an R2 delete that
fails is logged and skipped, never propagated, so a prune problem can't
fail the agent's upload-confirm call.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backup_storage import R2NotConfigured, delete_object
from app.config import get_settings
from app.models import BackupShop, BackupUpload

log = logging.getLogger(__name__)
_settings = get_settings()


def effective_retention_count(shop: BackupShop) -> int:
    return shop.backup_retention_count or _settings.tally_backup_retention_count


def prune_confirmed_backups(session: Session, shop: BackupShop) -> int:
    """Delete confirmed backups for `shop` beyond the newest `keep`.

    Returns the number of `BackupUpload` rows removed. Callers pass a live
    session and are responsible for the commit.
    """
    keep = effective_retention_count(shop)
    if keep < 0:
        return 0

    stale = list(
        session.scalars(
            select(BackupUpload)
            .where(
                BackupUpload.shop_id == shop.id,
                BackupUpload.status == "confirmed",
            )
            .order_by(BackupUpload.uploaded_at.desc())
            .offset(keep)
        )
    )

    removed = 0
    for row in stale:
        try:
            delete_object(row.r2_key)
        except R2NotConfigured:
            # Storage not configured — nothing to delete in the cloud, but
            # still drop the row so the list stays bounded.
            pass
        except Exception:  # noqa: BLE001 — best-effort cleanup
            log.warning("backup retention: could not delete R2 object %s", row.r2_key)
        session.delete(row)
        removed += 1

    if removed:
        log.info(
            "backup retention: pruned %d confirmed backup(s) for shop %s (keeping %d)",
            removed,
            shop.id,
            keep,
        )
    return removed

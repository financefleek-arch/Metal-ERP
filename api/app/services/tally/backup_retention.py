"""Cloud retention for the companion agent's backup uploads.

One TallyPrime backup run is several files — a `TBK…900` manifest plus one
or more `TDBK…001/.002` data parts — all tagged by the agent with a shared
`set_id`. Retention keeps at most N *sets* per shop and, when pruning,
deletes a whole set at once (every R2 object + row): a half-set can't be
restored, so it's all-or-nothing.

N is `BackupShop.backup_retention_count` when set, else
`settings.tally_backup_retention_count`.

Only `confirmed` rows count and are pruned — `pending`/`failed` rows carry
no real storage and a `failed` row may still retry. A row with no `set_id`
(pre-0030, or a standalone upload) is its own singleton set. Best-effort:
an R2 delete that fails is logged and skipped, never propagated, so a
prune problem can't fail the agent's upload-confirm call.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backup_storage import R2NotConfigured, delete_object
from app.config import get_settings
from app.models import BackupShop, BackupUpload

log = logging.getLogger(__name__)
_settings = get_settings()


def effective_retention_count(shop: BackupShop) -> int:
    return shop.backup_retention_count or _settings.tally_backup_retention_count


def _set_key(row: BackupUpload) -> tuple[bool, str]:
    """Group key: (True, set_id) for a run, or (False, row id) for a row
    with no set_id so it can never merge with another set."""
    return (True, row.set_id) if row.set_id else (False, row.id)


def prune_confirmed_backups(session: Session, shop: BackupShop) -> int:
    """Delete confirmed backup *sets* for `shop` beyond the newest `keep`.

    Returns the number of `BackupUpload` rows removed (a whole set at a
    time). Callers pass a live session and own the commit.
    """
    keep = effective_retention_count(shop)
    if keep < 0:
        return 0

    rows = list(
        session.scalars(
            select(BackupUpload).where(
                BackupUpload.shop_id == shop.id,
                BackupUpload.status == "confirmed",
            )
        )
    )

    sets: dict[tuple[bool, str], list[BackupUpload]] = defaultdict(list)
    for row in rows:
        sets[_set_key(row)].append(row)

    # Newest first, by each set's most-recent file. A set with no timestamp
    # (shouldn't happen for confirmed rows) sorts oldest.
    _EPOCH = datetime.min.replace(tzinfo=UTC)

    def set_recency(members: list[BackupUpload]) -> datetime:
        return max((m.uploaded_at for m in members if m.uploaded_at), default=_EPOCH)

    ordered = sorted(sets.values(), key=set_recency, reverse=True)
    stale_sets = ordered[keep:]

    removed = 0
    for members in stale_sets:
        for row in members:
            try:
                delete_object(row.r2_key)
            except R2NotConfigured:
                pass  # nothing in the cloud to delete; still drop the row
            except Exception:  # noqa: BLE001 — best-effort cleanup
                log.warning("backup retention: could not delete R2 object %s", row.r2_key)
            session.delete(row)
            removed += 1

    if removed:
        log.info(
            "backup retention: pruned %d file(s) across %d set(s) for shop %s (keeping %d sets)",
            removed,
            len(stale_sets),
            shop.id,
            keep,
        )
    return removed

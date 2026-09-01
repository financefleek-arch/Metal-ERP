"""Nightly stale-alias sweep.

Deletes `item_alias` rows with `source = learned` whose `last_used_at` has
fallen behind the cutoff (default 90 days) — a one-off billing typo like
"hawkins 55" self-cleans. Aliases off a real document
(`auto_from_purchase`, `auto_from_invoice`) are never swept. A `learned`
alias with a NULL `last_used_at` (shouldn't happen — Loop 2 always stamps
it) is treated as stale.

No scheduler in the repo yet: run this from cron / a systemd timer via

    python -m app.services.catalogue.alias_sweep [--days N] [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import ItemAlias
from app.models._mixins import AliasSource

DEFAULT_STALE_DAYS = 90


def sweep_stale_aliases(
    session: Session, *, days: int = DEFAULT_STALE_DAYS, dry_run: bool = False
) -> list[str]:
    """Remove stale learned aliases. Returns the ids that were (or would be)
    deleted. Caller commits.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    cond = ItemAlias.source == AliasSource.learned
    stale = or_(
        ItemAlias.last_used_at.is_(None),
        ItemAlias.last_used_at < cutoff,
    )
    ids = list(
        session.scalars(select(ItemAlias.id).where(cond, stale)).all()
    )
    if ids and not dry_run:
        session.execute(delete(ItemAlias).where(ItemAlias.id.in_(ids)))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep stale learned item aliases")
    parser.add_argument("--days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as session:
        ids = sweep_stale_aliases(session, days=args.days, dry_run=args.dry_run)
        if not args.dry_run:
            session.commit()
        verb = "would delete" if args.dry_run else "deleted"
        print(f"alias_sweep: {verb} {len(ids)} stale learned alias(es)")


if __name__ == "__main__":
    main()

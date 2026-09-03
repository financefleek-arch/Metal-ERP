"""Provision (or rotate the key of) a shop for the Tally companion agent.

Shops are staff-provisioned only — there is no self-serve signup. Run once
per new customer install:

    python -m tools.make_backup_shop --name "Sugal Foods" [--tenant-id ...]

Prints the plaintext API key exactly once; it is never stored or returned
again (only its hash is kept, same contract as a user password). Give this
key to whoever installs the tally-agent Windows service at the shop — it
goes into that install's appsettings.json as ShopApiKey.

Re-running with --rotate-key against an existing shop name issues a new
key (invalidating the old one) without creating a duplicate shop row.
"""

from __future__ import annotations

import argparse
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import BackupShop
from app.security import hash_shop_key


def run(
    session: Session, *, name: str, tenant_id: str | None, rotate_key: bool
) -> tuple[str, str | None, bool]:
    """Returns (shop_id, plaintext_key_or_None, created)."""
    shop = session.scalars(select(BackupShop).where(BackupShop.name == name)).first()

    if shop is None:
        key = secrets.token_urlsafe(32)
        shop = BackupShop(
            name=name, api_key_hash=hash_shop_key(key), tenant_id=tenant_id, is_active=True
        )
        session.add(shop)
        session.flush()
        return shop.id, key, True

    if tenant_id is not None:
        shop.tenant_id = tenant_id

    key: str | None = None
    if rotate_key:
        key = secrets.token_urlsafe(32)
        shop.api_key_hash = hash_shop_key(key)

    session.flush()
    return shop.id, key, False


def main() -> None:
    ap = argparse.ArgumentParser(description="Provision a Tally companion agent shop")
    ap.add_argument("--name", required=True, help="shop display name, e.g. 'Sugal Foods'")
    ap.add_argument(
        "--tenant-id", default=None, help="optional: soft-link to a Metal ERP tenant id"
    )
    ap.add_argument(
        "--rotate-key",
        action="store_true",
        help="issue a new API key for an existing shop (invalidates the old one)",
    )
    args = ap.parse_args()

    with SessionLocal() as session:
        shop_id, key, created = run(
            session, name=args.name, tenant_id=args.tenant_id, rotate_key=args.rotate_key
        )
        session.commit()

    if key:
        verb = "created" if created else "key rotated for"
        print(f"shop {verb}: {args.name}  (shop {shop_id})")
        print(f"API key (save now, shown once): {key}")
    else:
        print(f"shop already exists, no key change: {args.name}  (shop {shop_id})")


if __name__ == "__main__":
    main()

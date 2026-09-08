"""Backfill `party.legal_name_normalized` through the real `normalize_name`
pipeline + the tenant's synonym map.

Migration 0023 seeds the column with a crude key (lower + strip non-alnum) so
`NOT NULL` can be set. This script replaces it with the same key
`resolve_party` / `create_party` compute, so fuzzy-match candidate quality and
exact-key de-dup line up with the live code paths.

    # dry run — report rows that would change + existing-duplicate clusters
    python -m tools.backfill_party_namekey

    # write the real keys
    python -m tools.backfill_party_namekey --apply

    # one tenant only
    python -m tools.backfill_party_namekey --tenant <id> --apply

Idempotent: re-running after a full apply is a no-op. Also (re-)seeds the
party legal-suffix synonyms for every tenant first, so the keys it writes
already reflect "Pvt Ltd" collapsing.

The collision report lists parties whose new key equals another party's new
key in the same tenant — these are your existing duplicates. This script does
NOT merge them; feed the list to the (future) party merge tool.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Party, Tenant
from app.models._mixins import PartyStatus
from app.seed import seed_synonyms

_BATCH = 1000


def run(session: Session, tenant_id: str, *, apply: bool = False) -> dict:
    # keep the tenant's synonym rows current (adds the party-suffix set to
    # tenants provisioned before this slice) so the key reflects them.
    if apply:
        seed_synonyms(session, tenant_id)
        session.flush()
    syn = load_synonym_map(session, tenant_id)

    parties = list(
        session.scalars(
            select(Party)
            .where(Party.tenant_id == tenant_id)
            .order_by(Party.legal_name)
        ).all()
    )

    changed = 0
    by_key: dict[str, list[str]] = defaultdict(list)
    pending = 0
    for p in parties:
        key = normalize_name(p.legal_name, syn)
        if p.status != PartyStatus.archived and key:
            by_key[key].append(p.legal_name)
        if key != p.legal_name_normalized:
            changed += 1
            if apply:
                p.legal_name_normalized = key
                pending += 1
                if pending >= _BATCH:
                    session.flush()
                    pending = 0

    if apply and pending:
        session.flush()

    collisions = {k: v for k, v in by_key.items() if len(v) > 1}
    return {
        "total": len(parties),
        "changed": changed,
        "collisions": collisions,
    }


def _print(tenant_id: str, r: dict) -> None:
    print(f"\n=== tenant {tenant_id} ===")
    print(f"  {r['total']} parties, {r['changed']} keys would change")
    if r["collisions"]:
        print(f"  {len(r['collisions'])} duplicate cluster(s) (same key, active):")
        for key, names in sorted(r["collisions"].items()):
            print(f"    [{key}]")
            for n in names:
                print(f"        - {n}")
    else:
        print("  no duplicate clusters")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill party.legal_name_normalized")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--tenant", help="tenant id (default: every tenant)")
    ap.add_argument("--apply", action="store_true", help="write (default: report only)")
    args = ap.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as session:
        tenant_ids = (
            [args.tenant]
            if args.tenant
            else list(session.scalars(select(Tenant.id)).all())
        )
        for tid in tenant_ids:
            _print(tid, run(session, tid, apply=args.apply))
        if args.apply:
            session.commit()
            print("\n(committed)")
        else:
            print("\n(dry run — nothing written; pass --apply to write)")


if __name__ == "__main__":
    main()

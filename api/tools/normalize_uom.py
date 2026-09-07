"""One-time (repeatable) backfill: fold every `item.uom` / `product_group.uom`
to its canonical spelling from the shared unit table.

Tally import historically stored `base_units` verbatim ("Doz", "PKT",
"Mtr", "NOS", "Kgs"), so a live catalogue has the same concept spelled
several ways. `app.domain.units.normalize_uom` maps a raw string to its
canonical value (falling back to a lowercased passthrough for a real but
un-tabled unit). This applies it in bulk.

    # dry run -> report, changes nothing
    python -m tools.normalize_uom --all --out uom-changes.csv

    # write the folded values
    python -m tools.normalize_uom --tenant <id> --apply

Only rows whose value actually changes are reported / written. Archived
and merged items are still folded (their unit shouldn't lie either).
`name_normalized`, `rate_mode` (gone), and every other column are
untouched — this is a pure spelling fold.
"""

from __future__ import annotations

import argparse
import csv
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.units import all_uoms, normalize_uom
from app.models import Item, ProductGroup, Tenant

_CSV_FIELDS = ["kind", "id", "name", "old_uom", "new_uom", "known"]

_KNOWN = set(all_uoms())


def _fold(session: Session, tenant_id: str, *, apply: bool) -> list[dict]:
    report: list[dict] = []

    items = session.scalars(
        select(Item).where(Item.tenant_id == tenant_id).order_by(Item.name)
    ).all()
    groups = session.scalars(
        select(ProductGroup)
        .where(ProductGroup.tenant_id == tenant_id)
        .order_by(ProductGroup.name)
    ).all()

    for kind, rows in (("item", items), ("group", groups)):
        for r in rows:
            old = r.uom
            new = normalize_uom(old) or None
            if (old or None) == new:
                continue
            report.append(
                {
                    "kind": kind,
                    "id": r.id,
                    "name": r.name,
                    "old_uom": old or "",
                    "new_uom": new or "",
                    "known": "yes" if (new in _KNOWN) else "no",
                }
            )
            if apply:
                r.uom = new

    if apply:
        session.flush()
    return report


def _summary(report: list[dict]) -> None:
    from collections import Counter

    by_pair = Counter((r["old_uom"], r["new_uom"]) for r in report)
    unknown = sorted({r["new_uom"] for r in report if r["known"] == "no"})
    print(f"\n  {len(report)} rows would change")
    for (old, new), n in by_pair.most_common():
        print(f"    {n:5d}  {old!r:>12} -> {new!r}")
    if unknown:
        print(f"  still not in the canonical table (left as-is, lowercased): {unknown}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fold item/group UOM to canonical spelling")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="tenant id")
    g.add_argument("--all", action="store_true", help="every tenant")
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    ap.add_argument("--out", help="write the per-row report to this CSV path")
    args = ap.parse_args()

    from app.db import SessionLocal

    all_rows: list[dict] = []
    with SessionLocal() as session:
        tenant_ids = (
            list(session.scalars(select(Tenant.id)).all())
            if args.all
            else [args.tenant]
        )
        for tid in tenant_ids:
            rows = _fold(session, tid, apply=args.apply)
            print(f"\n=== tenant {tid} ===")
            _summary(rows)
            all_rows.extend(rows)
        if args.apply:
            session.commit()
            print("\n(committed)")
        else:
            print("\n(dry run — nothing written; pass --apply to persist)")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
            w.writeheader()
            w.writerows(all_rows)
        print(f"wrote {args.out} ({len(all_rows)} rows)")

    sys.exit(0)


if __name__ == "__main__":
    main()

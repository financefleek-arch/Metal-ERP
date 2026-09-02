"""One-time (and repeatable) backfill: run the rules-first classifier over
every existing `item` of a tenant and set `category_id` / `group_id` /
`status`.

Same code path as the live create-time classifier
(`services.catalogue.classify_apply.Classifier`) — this is not a bespoke
script, it just applies it in bulk.

    # dry run -> CSV, changes nothing
    python -m tools.reclassify_items --tenant <id> --out sethia.csv

    # write category_id / group_id / status
    python -m tools.reclassify_items --tenant <id> --apply

Backfill status policy (agreed 2026-09-02):
  * always set category_id + group_id
  * high-confidence  -> status flips to `confirmed`
  * medium / low     -> status stays `unconfirmed`
  * `--keep-status`  -> never touch status (assign only)
  * `--all-unconfirmed` -> force status unconfirmed on every touched item

Archived / merged items are skipped.
"""

from __future__ import annotations

import argparse
import csv

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, Tenant
from app.models._mixins import ItemStatus
from app.services.catalogue.classify_apply import Classifier

_CSV_FIELDS = [
    "item_id", "name", "hsn", "uom",
    "old_category_id", "old_group_id", "old_status",
    "new_department", "new_group", "new_brand",
    "new_category_id", "new_group_id", "new_status",
    "confidence", "source", "rule_hit",
]


def run(
    session: Session,
    tenant_id: str,
    *,
    apply: bool = False,
    keep_status: bool = False,
    all_unconfirmed: bool = False,
) -> list[dict]:
    clf = Classifier(session, tenant_id)
    items = list(
        session.scalars(
            select(Item)
            .where(
                Item.tenant_id == tenant_id,
                Item.status != ItemStatus.archived,
                Item.merged_into_id.is_(None),
            )
            .order_by(Item.name)
        ).all()
    )

    report: list[dict] = []
    for it in items:
        applied = clf.apply(
            it.name, hsn=it.hsn_code, uom=it.uom,
            force_unconfirmed=all_unconfirmed,
        )
        res = applied.result

        new_status = it.status
        if not keep_status:
            if all_unconfirmed:
                new_status = ItemStatus.unconfirmed
            elif applied.status == ItemStatus.confirmed:
                new_status = ItemStatus.confirmed
            # else: leave whatever it was (usually unconfirmed)

        report.append({
            "item_id": it.id,
            "name": it.name,
            "hsn": it.hsn_code or "",
            "uom": it.uom or "",
            "old_category_id": it.category_id or "",
            "old_group_id": it.group_id or "",
            "old_status": str(it.status),
            "new_department": res.department,
            "new_group": res.group,
            "new_brand": res.brand or "",
            "new_category_id": applied.category_id or "",
            "new_group_id": applied.group_id or "",
            "new_status": str(new_status),
            "confidence": f"{res.confidence:.2f}",
            "source": res.source,
            "rule_hit": res.rule_hit or "",
        })

        if apply:
            it.category_id = applied.category_id
            it.group_id = applied.group_id
            it.status = new_status

    if apply:
        session.flush()
    return report


def _summary(report: list[dict]) -> None:
    from collections import Counter

    dept = Counter(r["new_department"] for r in report)
    src = Counter(r["source"] for r in report)
    conf_confirm = sum(1 for r in report if float(r["confidence"]) >= 0.70)
    print(f"\n  {len(report)} items")
    print(f"  by source: {dict(src)}")
    print(f"  would confirm (confidence >= 0.70): {conf_confirm}")
    print("  by department:")
    for d, n in dept.most_common():
        print(f"    {n:5d}  {d}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Reclassify a tenant's items")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant", help="tenant id")
    g.add_argument("--all", action="store_true", help="every tenant")
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    ap.add_argument("--out", help="write the per-item report to this CSV path")
    ap.add_argument("--keep-status", action="store_true", help="never change item.status")
    ap.add_argument(
        "--all-unconfirmed", action="store_true",
        help="force status=unconfirmed on every touched item (one review pass)",
    )
    args = ap.parse_args()

    from app.db import SessionLocal

    all_rows: list[dict] = []
    with SessionLocal() as session:
        tenant_ids = (
            list(session.scalars(select(Tenant.id)).all())
            if args.all else [args.tenant]
        )
        for tid in tenant_ids:
            rows = run(
                session, tid,
                apply=args.apply,
                keep_status=args.keep_status,
                all_unconfirmed=args.all_unconfirmed,
            )
            print(f"\n=== tenant {tid} ===")
            _summary(rows)
            all_rows.extend(rows)
        if args.apply:
            session.commit()
            print("\n(committed)")
        else:
            print("\n(dry run — nothing written; pass --apply to write)")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            w.writeheader()
            w.writerows(all_rows)
        print(f"wrote {len(all_rows)} rows to {args.out}")


if __name__ == "__main__":
    main()

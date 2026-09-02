"""One-time (and repeatable) bartan-synonym backfill.

Two things drift when the name-normalization dictionary changes (e.g. the
bartan block was added, or a shop adds a word):

  1. tenants created before the dictionary shipped have no `synonym` rows
  2. every existing `item` / `product_group` / `item_alias` still carries a
     `*_normalized` value computed under the *old* dictionary, so a shop-
     keeper typing "balti" (-> "bucket") won't match an item stored as
     "ss balti no 3".

This module fixes both. It is dry-run by default because re-normalizing can
collapse two rows onto one key, and `(tenant_id, name_normalized)` is UNIQUE
on all three tables — those collisions need a human merge first.

    python -m app.services.catalogue.backfill_bartan            # report only
    python -m app.services.catalogue.backfill_bartan --apply    # write non-colliding rows
    python -m app.services.catalogue.backfill_bartan --tenant <id> [--apply]

Run order against a live DB:
  1. this tool (report)  ->  note the "MERGE FIRST" list
  2. merge each colliding pair via POST /api/items/{id}/merge
  3. this tool --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Item, ItemAlias, ProductGroup, Tenant
from app.seed import seed_synonyms

# (label, model, name-attr, normalized-attr) for the three normalized tables.
_TARGETS = [
    ("item", Item, "name", "name_normalized"),
    ("product_group", ProductGroup, "name", "name_normalized"),
    ("item_alias", ItemAlias, "alias_text", "alias_normalized"),
]


@dataclass
class RowChange:
    table: str
    row_id: str
    label: str  # the human name / alias text
    old_key: str
    new_key: str


@dataclass
class Collision:
    table: str
    new_key: str
    moving: list[RowChange]  # rows that would move onto new_key
    # an existing row that already holds new_key and is NOT moving, if any
    incumbent: tuple[str, str] | None = None  # (row_id, name)


@dataclass
class TenantResult:
    tenant_id: str
    synonyms_added: int
    changes: list[RowChange] = field(default_factory=list)
    # keyed "<table>:<new_key>"; a clash is >1 row moving onto new_key, OR a
    # row moving onto a key an untouched row already holds (the incumbent).
    collisions: dict[str, Collision] = field(default_factory=dict)
    applied: int = 0

    @property
    def safe_changes(self) -> list[RowChange]:
        colliding_ids = {
            c.row_id for col in self.collisions.values() for c in col.moving
        }
        return [c for c in self.changes if c.row_id not in colliding_ids]


def _plan_table(
    session: Session,
    tenant_id: str,
    label: str,
    model: type,
    name_attr: str,
    norm_attr: str,
    syn_map: dict[str, str],
) -> tuple[list[RowChange], dict[str, tuple[str, str]]]:
    """Return the changes for one table plus a {normalized_key -> (row_id,
    name)} map of the rows that would NOT change (used for collision
    detection and for showing the other side of a collision).
    """
    rows = list(
        session.scalars(select(model).where(model.tenant_id == tenant_id)).all()
    )
    changes: list[RowChange] = []
    unchanged_keys: dict[str, tuple[str, str]] = {}
    for r in rows:
        name = getattr(r, name_attr) or ""
        old_key = getattr(r, norm_attr) or ""
        new_key = normalize_name(name, syn_map)
        if new_key and new_key != old_key:
            changes.append(RowChange(label, r.id, name, old_key, new_key))
        elif old_key:
            unchanged_keys[old_key] = (r.id, name)
    return changes, unchanged_keys


def plan_tenant(session: Session, tenant_id: str, *, seed: bool = True) -> TenantResult:
    """Seed synonym rows (optional) and compute the re-normalization plan for
    one tenant. No writes to the normalized columns — caller applies.
    """
    added = seed_synonyms(session, tenant_id) if seed else 0
    if added:
        session.flush()
    syn_map = load_synonym_map(session, tenant_id)

    result = TenantResult(tenant_id=tenant_id, synonyms_added=added)
    # per-table changes + the keys still held by rows that stay put (for
    # collision detection — each table has its own UNIQUE(tenant, key)).
    all_changes: list[RowChange] = []
    per_table_held: dict[str, dict[str, tuple[str, str]]] = {}
    for label, model, name_attr, norm_attr in _TARGETS:
        changes, unchanged = _plan_table(
            session, tenant_id, label, model, name_attr, norm_attr, syn_map
        )
        all_changes.extend(changes)
        per_table_held[label] = unchanged

    result.changes = all_changes

    # Collision detection is per-table (each table has its own UNIQUE(tenant,key)).
    by_table_newkey: dict[tuple[str, str], list[RowChange]] = {}
    for c in all_changes:
        by_table_newkey.setdefault((c.table, c.new_key), []).append(c)

    for (table, new_key), rows in by_table_newkey.items():
        incumbent = per_table_held.get(table, {}).get(new_key)
        if len(rows) > 1 or incumbent is not None:
            result.collisions[f"{table}:{new_key}"] = Collision(
                table=table, new_key=new_key, moving=rows, incumbent=incumbent
            )

    return result


def apply_tenant(session: Session, result: TenantResult) -> int:
    """Write the non-colliding `new_key` values. Caller commits."""
    model_by_label = {label: model for label, model, *_ in _TARGETS}
    norm_by_label = {label: norm for label, _, _, norm in _TARGETS}
    n = 0
    for c in result.safe_changes:
        row = session.get(model_by_label[c.table], c.row_id)
        if row is None:
            continue
        setattr(row, norm_by_label[c.table], c.new_key)
        n += 1
    result.applied = n
    return n


def run(
    session: Session, *, tenant_id: str | None = None, apply: bool = False
) -> list[TenantResult]:
    tenant_ids = (
        [tenant_id] if tenant_id else list(session.scalars(select(Tenant.id)).all())
    )

    results: list[TenantResult] = []
    for tid in tenant_ids:
        res = plan_tenant(session, tid)
        if apply:
            apply_tenant(session, res)
        results.append(res)
    return results


def _print_report(results: list[TenantResult], *, applied: bool) -> None:
    for res in results:
        print(f"\n=== tenant {res.tenant_id} ===")
        print(f"  synonym rows added: {res.synonyms_added}")
        print(f"  rows that re-normalize: {len(res.changes)}")
        if res.collisions:
            print(f"  ** {len(res.collisions)} COLLISION(S) — MERGE FIRST, then re-run:")
            for col in res.collisions.values():
                print(f"    [{col.table}] key {col.new_key!r} would be shared by:")
                if col.incumbent:
                    rid, name = col.incumbent
                    print(f"        {rid}  {name!r}  (already normalized — keep or merge into)")
                for c in col.moving:
                    print(f"        {c.row_id}  {c.label!r}  (was {c.old_key!r} — would move here)")
        safe = res.safe_changes
        if safe and not applied:
            print(f"  {len(safe)} row(s) would update (run with --apply):")
            for c in safe[:40]:
                print(f"    [{c.table}] {c.label!r}: {c.old_key!r} -> {c.new_key!r}")
            if len(safe) > 40:
                print(f"    … and {len(safe) - 40} more")
        if applied:
            print(f"  rows updated: {res.applied}")
            if res.collisions:
                print("  (colliding rows left unchanged — merge and re-run)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill bartan synonyms + re-normalize existing catalogue rows"
    )
    parser.add_argument("--tenant", help="limit to one tenant id (default: all)")
    parser.add_argument(
        "--apply", action="store_true", help="write non-colliding changes (default: report only)"
    )
    args = parser.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as session:
        results = run(session, tenant_id=args.tenant, apply=args.apply)
        if args.apply:
            session.commit()
        _print_report(results, applied=args.apply)

    total_coll = sum(len(r.collisions) for r in results)
    if total_coll:
        print(
            f"\n{total_coll} collision(s) across {len(results)} tenant(s) still need a "
            "manual merge. Re-run after merging."
        )


if __name__ == "__main__":
    main()

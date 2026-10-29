"""Per-supplier defaults, so a repeat import needs no decisions.

A supplier's usual margin and rounding, how its group names map to ours, whether items are added
to the item list automatically after an import. Applied
once, when a catalog is imported from that supplier; the catalog's own margin can still be changed
afterwards.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SupplierCatalog, SupplierCatalogDefault
from app.services.catalog import promote as promote_svc
from app.services.catalog.reprice import reprice

ROUNDING_STEPS = (1, 5, 10)


def key(name: str) -> str:
    """Supplier group names compare case- and space-insensitively."""
    return " ".join((name or "").lower().split())


def get(session: Session, tenant_id: str, party_id: str) -> SupplierCatalogDefault | None:
    return session.scalar(
        select(SupplierCatalogDefault).where(
            SupplierCatalogDefault.tenant_id == tenant_id,
            SupplierCatalogDefault.party_id == party_id,
        )
    )


def group_lookup(defaults: SupplierCatalogDefault | None) -> dict[str, str]:
    """normalised supplier group -> our group name."""
    if defaults is None:
        return {}
    return {key(k): v for k, v in (defaults.group_map or {}).items() if k.strip() and v.strip()}


def apply_to_catalog(
    session: Session, catalog: SupplierCatalog, defaults: SupplierCatalogDefault | None
) -> bool:
    """Set the catalog's margin and rounding from the supplier's defaults and reprice. Returns
    whether anything was applied."""
    if defaults is None:
        return False
    changed = False
    if defaults.bulk_margin_pct is not None:
        catalog.bulk_margin_pct = Decimal(str(defaults.bulk_margin_pct))
        changed = True
    if defaults.rounding_step is not None:
        catalog.rounding_step = int(defaults.rounding_step)
        changed = True
    if changed:
        reprice(session, catalog)
    return changed


def add_automatically(
    session: Session, catalog: SupplierCatalog, defaults: SupplierCatalogDefault | None
) -> promote_svc.PromotePlan | None:
    """Promote every included row to an item. Returns the plan, or None when the supplier does not
    add automatically."""
    if defaults is None or not defaults.add_automatically:
        return None
    rows = [r for r in catalog.items if r.included]
    if not rows:
        return None
    return promote_svc.promote(session, catalog.tenant_id, rows)

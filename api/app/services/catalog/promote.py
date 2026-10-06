"""Promote catalog rows into the shop's item master.

A catalog row is a supplier's offer; the `Item` is what the shop bills and what Tally gets as a
stock item. Promoting links each product to exactly one `Item`:

  - the product already has an item (another catalog promoted it): reuse it, refresh its rate;
  - an unlinked item with the same normalised name exists: link to it (never a duplicate), and
    leave its rates alone: they are the shop's own;
  - otherwise a new item: name = display name, code -> barcode and sku, rate = our new price,
    last purchase rate = the supplier price, unit `nos`.

Two products with the same display name (the supplier sells the same glass in several colours)
cannot both be called that: items need unique names, in Tally too. They get the code appended,
"Juice Glass 190 ML (JWG-0008)".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.normalize import load_synonym_map, normalize_name
from app.models import CatalogProduct, Item, ItemCategory, SupplierCatalogItem
from app.models._mixins import Availability, ItemSource, ItemStatus, ItemType

_NAME_MAX = 300


@dataclass
class PromotePlan:
    """What promoting these rows would do."""

    create: int = 0
    link_existing: int = 0  # an item with this name already exists: link, do not duplicate
    reuse: int = 0  # the product already has an item (refresh its rate)
    already: int = 0  # this row is already promoted
    renamed: int = 0  # created with the code appended to keep names unique
    examples: list[str] = field(default_factory=list)  # a few names that will be linked
    created_ids: list[str] = field(default_factory=list)  # items created by this call (for Undo)


@dataclass
class _Entry:
    row: SupplierCatalogItem
    product: CatalogProduct
    name: str = ""
    norm: str = ""
    action: str = ""  # create | link | reuse | already
    target: Item | None = None
    renamed: bool = False


def _item_name(display: str, tag: str) -> str:
    return f"{display.strip()} ({tag.strip()})"[:_NAME_MAX]


def _plan(
    session: Session, tenant_id: str, rows: Sequence[SupplierCatalogItem]
) -> tuple[list[_Entry], PromotePlan]:
    syn = load_synonym_map(session, tenant_id)
    products: dict[str, CatalogProduct] = {}
    pids = [r.product_id for r in rows if r.product_id]
    for i in range(0, len(pids), 500):
        for p in session.scalars(
            select(CatalogProduct).where(CatalogProduct.id.in_(pids[i : i + 500]))
        ):
            products[p.id] = p

    entries: list[_Entry] = []
    for r in rows:
        prod = products.get(r.product_id or "")
        if prod is None:
            continue
        entries.append(_Entry(row=r, product=prod, name=r.display_name.strip()))

    plan = PromotePlan()
    pending: list[_Entry] = []
    for e in entries:
        if e.row.item_id:
            e.action = "already"
            plan.already += 1
        elif e.product.item_id:
            item = session.get(Item, e.product.item_id)
            if item is not None:
                e.action, e.target = "reuse", item
                plan.reuse += 1
            else:  # the item was deleted: promote afresh
                e.product.item_id = None
                pending.append(e)
        else:
            pending.append(e)

    # in-batch name clashes: the same normalised name on several products
    by_norm: dict[str, list[_Entry]] = {}
    for e in pending:
        e.norm = normalize_name(e.name, syn)
        by_norm.setdefault(e.norm, []).append(e)

    existing_by_norm: dict[str, Item] = {}
    norms = [n for n in by_norm if n]
    for i in range(0, len(norms), 500):
        for it in session.scalars(
            select(Item).where(
                Item.tenant_id == tenant_id, Item.name_normalized.in_(norms[i : i + 500])
            )
        ):
            existing_by_norm[it.name_normalized] = it
    linked_item_ids = set(
        session.scalars(
            select(CatalogProduct.item_id).where(
                CatalogProduct.tenant_id == tenant_id, CatalogProduct.item_id.is_not(None)
            )
        )
    )

    taken: set[str] = set()  # names given to renamed items in this pass
    for norm, group in by_norm.items():
        existing = existing_by_norm.get(norm)
        free_existing = existing is not None and existing.id not in linked_item_ids
        if len(group) == 1 and free_existing:
            e = group[0]
            e.action, e.target = "link", existing
            plan.link_existing += 1
            if len(plan.examples) < 5:
                plan.examples.append(existing.name if existing else e.name)
            continue
        clash = len(group) > 1 or existing is not None
        for e in group:
            e.action = "create"
            if clash:
                # tell them apart by the supplier's own code (it is what the shop and its bills
                # call the product); our own code, always unique, when that would still clash
                base = e.name
                for tag in (e.product.supplier_code, e.product.code):
                    e.name = _item_name(base, tag)
                    e.norm = normalize_name(e.name, syn)
                    if e.norm not in taken and e.norm not in existing_by_norm:
                        break
                taken.add(e.norm)
                e.renamed = True
                plan.renamed += 1
            plan.create += 1
    return entries, plan


def preview(session: Session, tenant_id: str, rows: Sequence[SupplierCatalogItem]) -> PromotePlan:
    return _plan(session, tenant_id, rows)[1]


def promote(
    session: Session, tenant_id: str, rows: Sequence[SupplierCatalogItem]
) -> PromotePlan:
    """Do it. The caller commits."""
    entries, plan = _plan(session, tenant_id, rows)
    for e in entries:
        if e.action == "already":
            continue
        row, prod = e.row, e.product
        if e.action == "create":
            item = Item(
                tenant_id=tenant_id,
                name=e.name,
                name_normalized=e.norm,
                item_type=ItemType.mrp,
                category_id=prod.category_id,
                uom="nos",
                size_text=(row.size_text or None),
                pack_qty=row.pack_qty or None,
                carton_qty=row.carton_qty or None,
                # price-list items are not stock until you mark them (in bulk) as in stock
                availability=Availability.out_of_stock,
                default_rate=_f(row.sell_price),
                last_purchase_rate=_f(row.cost_price),
                barcode=prod.code,
                sku=prod.code,
                source=ItemSource.catalog,
                status=ItemStatus.confirmed,
            )
            cat = session.get(ItemCategory, prod.category_id) if prod.category_id else None
            if cat is not None and cat.hsn_code:  # the group's HSN / GST default
                item.hsn_code = cat.hsn_code
                if cat.gst_rate is not None:
                    item.gst_rate = float(cat.gst_rate)
            session.add(item)
            session.flush()
            e.target = item
            plan.created_ids.append(item.id)
        else:
            item = e.target
            assert item is not None
            if e.action == "reuse":  # our own item: the new offer's rates
                item.default_rate = _f(row.sell_price)
                item.last_purchase_rate = _f(row.cost_price)
            if item.pack_qty is None and row.pack_qty:
                item.pack_qty = row.pack_qty
            if item.carton_qty is None and row.carton_qty:
                item.carton_qty = row.carton_qty
            if not item.barcode:
                item.barcode = prod.code
            if not item.sku:
                item.sku = prod.code
        assert e.target is not None
        prod.item_id = e.target.id
        row.item_id = e.target.id
    session.flush()
    return plan


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)

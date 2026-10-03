"""The shop's product registry and the group-based item codes.

A *product* is what the shop sells; a catalog row is one supplier's offer of it. The code
belongs to the product, so a later PDF that offers the same thing (same supplier, same supplier
code) reuses the product and its code, group and name instead of getting new ones.

Codes: `<group code>-<number>`, e.g. `BM-0042` for Beer Mugs. The group code is a short code
kept on the group (`item_category.code_prefix`), suggested from its name the first time it is
needed. Products with no group use the firm's fallback prefix (`tenant.catalog_code_prefix`, or
`GEN`). Each prefix has its own counter and numbers are never reused.

A code is *provisional* until first real use (printed on a label, put in a customer catalog,
promoted to an item, sent to Tally). Then `code_locked` is set and it never changes. While it is
provisional, moving the product to another group re-issues the code in that group's series.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.normalize import normalize_name
from app.models import CatalogProduct, ItemCategory, SupplierCatalogItem, Tenant
from app.services.catalog.codes import allocate_codes, normalize_prefix

FALLBACK_PREFIX = "GEN"
_CHUNK = 500


# --------------------------------------------------------------------------- group codes


def suggest_group_code(name: str, taken: set[str]) -> str:
    """A short code (2-4 letters) for a group name, not in `taken`.

    "Beer Mugs" -> BM, "Bowls & Bowl Sets" -> BBS, "Jars & Storage" -> JS, "Bottles" -> BOT.
    On a clash it tries other shapes of the name, then adds a digit.
    """
    words = [w.upper() for w in re.findall(r"[A-Za-z0-9]+", name)]
    if not words:
        words = ["GRP"]
    initials = "".join(w[0] for w in words)
    candidates: list[str] = []
    if len(words) >= 2:
        candidates.append(initials[:4])
        candidates.append((words[0][:2] + initials[1:])[:4])
    candidates += [words[0][:3], words[0][:4], words[0][:2]]
    for cand in candidates:
        cand = cand.ljust(2, "X")
        if cand not in taken:
            return cand
    base = candidates[0][:3].ljust(2, "X")
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def fallback_prefix(tenant: Tenant) -> str:
    """Prefix for products that have no group."""
    return normalize_prefix(tenant.catalog_code_prefix) or FALLBACK_PREFIX


def taken_prefixes(session: Session, tenant: Tenant) -> set[str]:
    rows = session.scalars(
        select(ItemCategory.code_prefix).where(
            ItemCategory.tenant_id == tenant.id, ItemCategory.code_prefix.is_not(None)
        )
    )
    return {p.upper() for p in rows if p} | {fallback_prefix(tenant)}


def group_code(session: Session, tenant: Tenant, category: ItemCategory) -> str:
    """The group's code, assigning (and saving) a suggested one the first time."""
    if category.code_prefix:
        return category.code_prefix
    category.code_prefix = suggest_group_code(category.name, taken_prefixes(session, tenant))
    session.flush()
    return category.code_prefix


def prefix_for(session: Session, tenant: Tenant, category: ItemCategory | None) -> str:
    return group_code(session, tenant, category) if category else fallback_prefix(tenant)


def issue_codes(
    session: Session, tenant: Tenant, category: ItemCategory | None, count: int
) -> list[str]:
    """`count` fresh codes in the group's series, in order."""
    return allocate_codes(session, tenant.id, prefix_for(session, tenant, category), count)


def prefix_in_use(session: Session, tenant_id: str, prefix: str) -> bool:
    """True once any product has a code with this prefix (so the group code is fixed)."""
    return (
        session.scalar(
            select(CatalogProduct.id)
            .where(CatalogProduct.tenant_id == tenant_id, CatalogProduct.code.like(f"{prefix}-%"))
            .limit(1)
        )
        is not None
    )


# --------------------------------------------------------------------------- products


def products_by_supplier_code(
    session: Session, tenant_id: str, supplier_party_id: str
) -> dict[str, CatalogProduct]:
    rows = session.scalars(
        select(CatalogProduct).where(
            CatalogProduct.tenant_id == tenant_id,
            CatalogProduct.supplier_party_id == supplier_party_id,
        )
    )
    return {p.supplier_code: p for p in rows}


def new_product(
    *,
    tenant_id: str,
    supplier_party_id: str | None,
    supplier_code: str,
    code: str,
    category_id: str | None,
    display_name: str,
) -> CatalogProduct:
    return CatalogProduct(
        tenant_id=tenant_id,
        supplier_party_id=supplier_party_id,
        supplier_code=supplier_code,
        code=code,
        code_locked=False,
        category_id=category_id,
        display_name=display_name,
        name_normalized=normalize_name(display_name),
    )


def rename_product(product: CatalogProduct, display_name: str) -> None:
    product.display_name = display_name
    product.name_normalized = normalize_name(display_name)


def suggest_by_name(
    session: Session, tenant_id: str, wanted: dict[str, str], exclude_ids: set[str]
) -> dict[str, CatalogProduct]:
    """For each key -> normalised name in `wanted`, an existing product (not in `exclude_ids`)
    with the same normalised name: the likely same product from another supplier or file."""
    norms = {n for n in wanted.values() if n}
    found: dict[str, CatalogProduct] = {}
    for i in range(0, len(norms), _CHUNK):
        chunk = list(norms)[i : i + _CHUNK]
        for p in session.scalars(
            select(CatalogProduct)
            .where(CatalogProduct.tenant_id == tenant_id, CatalogProduct.name_normalized.in_(chunk))
            .order_by(CatalogProduct.created_at, CatalogProduct.id)
        ):
            if p.id not in exclude_ids:
                found.setdefault(p.name_normalized, p)
    return {key: found[n] for key, n in wanted.items() if n in found}


# --------------------------------------------------------------------------- locking


def lock_products(session: Session, product_ids: Iterable[str]) -> None:
    """Fix these products' codes for good (first real use)."""
    ids = list(dict.fromkeys(product_ids))
    for i in range(0, len(ids), _CHUNK):
        session.execute(
            update(CatalogProduct)
            .where(
                CatalogProduct.id.in_(ids[i : i + _CHUNK]),
                CatalogProduct.code_locked.is_(False),
            )
            .values(code_locked=True)
        )


def lock_for_items(session: Session, item_ids: Iterable[str]) -> None:
    """Lock the products behind these catalog rows."""
    ids = list(dict.fromkeys(item_ids))
    product_ids: list[str] = []
    for i in range(0, len(ids), _CHUNK):
        product_ids += [
            pid
            for (pid,) in session.execute(
                select(SupplierCatalogItem.product_id).where(
                    SupplierCatalogItem.id.in_(ids[i : i + _CHUNK]),
                    SupplierCatalogItem.product_id.is_not(None),
                )
            )
            if pid
        ]
    lock_products(session, product_ids)


# --------------------------------------------------------------------------- regrouping


def regroup(
    session: Session,
    tenant: Tenant,
    products: Sequence[CatalogProduct],
    category: ItemCategory | None,
) -> int:
    """Move products to `category` (None = no group). Returns how many codes were re-issued.

    A provisional product whose code is not already in the new group's series gets a new code
    from that series; a locked product keeps its code. The catalog rows follow the product.
    """
    if not products:
        return 0
    prefix = prefix_for(session, tenant, category)
    need = [p for p in products if not p.code_locked and not p.code.startswith(f"{prefix}-")]
    codes = allocate_codes(session, tenant.id, prefix, len(need)) if need else []
    for product, code in zip(need, codes, strict=True):
        product.code = code
    cat_id = category.id if category else None
    for product in products:
        product.category_id = cat_id
    session.flush()

    # catalog rows follow: new codes for the re-issued ones, the new group for all
    for product, code in zip(need, codes, strict=True):
        session.execute(
            update(SupplierCatalogItem)
            .where(SupplierCatalogItem.product_id == product.id)
            .values(code=code)
        )
    ids = [p.id for p in products]
    for i in range(0, len(ids), _CHUNK):
        session.execute(
            update(SupplierCatalogItem)
            .where(SupplierCatalogItem.product_id.in_(ids[i : i + _CHUNK]))
            .values(category_id=cat_id, suggested_group=None)
        )
    return len(need)

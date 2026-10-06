"""Supplier catalog H5: supplier defaults, "update item prices", and Undo for adding items.

Same gate as the rest of the catalog module (`ext_supplier_catalog`; owner/accountant write).
"""

from __future__ import annotations

import hashlib
import io
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.deps import SessionDep
from app.models import Item, SupplierCatalogDefault, SupplierCatalogItem
from app.routers.catalog import (
    CatalogUser,
    CatalogWriteUser,
    _check_supplier,
    _get_catalog,
    _get_item,
    _label_rows,
)
from app.schemas_catalog import (
    PricesApplyOut,
    PricesPreviewOut,
    PromoteUndoIn,
    PromoteUndoOut,
    SelectionIn,
    SupplierDefaultsIn,
    SupplierDefaultsOut,
)
from app.services import audit
from app.services import media as media_svc
from app.services.catalog import supplier_defaults as svc
from app.services.catalog.storage import CatalogStorage, get_storage, image_key
from app.services.items import detach_catalog_links

router = APIRouter(prefix="/api/supplier-catalogs", tags=["supplier-catalog-review"])


# --------------------------------------------------------------------------
# supplier defaults
# --------------------------------------------------------------------------


def _defaults_out(row: SupplierCatalogDefault | None) -> SupplierDefaultsOut:
    if row is None:
        return SupplierDefaultsOut(
            bulk_margin_pct=None,
            rounding_step=None,
            group_map={},
            add_automatically=False,
            mark_in_stock=False,
        )
    return SupplierDefaultsOut(
        bulk_margin_pct=(
            f"{Decimal(str(row.bulk_margin_pct)):.2f}" if row.bulk_margin_pct is not None else None
        ),
        rounding_step=row.rounding_step,
        group_map=dict(row.group_map or {}),
        add_automatically=row.add_automatically,
        mark_in_stock=row.mark_in_stock,
    )


@router.get("/suppliers/{party_id}/defaults", response_model=SupplierDefaultsOut)
def get_defaults(party_id: str, session: SessionDep, user: CatalogUser) -> SupplierDefaultsOut:
    _check_supplier(session, user.tenant_id, party_id)
    return _defaults_out(svc.get(session, user.tenant_id, party_id))


@router.put("/suppliers/{party_id}/defaults", response_model=SupplierDefaultsOut)
def put_defaults(
    party_id: str, body: SupplierDefaultsIn, session: SessionDep, user: CatalogWriteUser
) -> SupplierDefaultsOut:
    _check_supplier(session, user.tenant_id, party_id)
    row = svc.get(session, user.tenant_id, party_id)
    if row is None:
        row = SupplierCatalogDefault(
            tenant_id=user.tenant_id, party_id=party_id, group_map={}
        )
        session.add(row)
    patch = body.model_fields_set
    if "bulk_margin_pct" in patch:
        row.bulk_margin_pct = body.bulk_margin_pct
    if "rounding_step" in patch:
        row.rounding_step = body.rounding_step
    if "group_map" in patch and body.group_map is not None:
        merged = dict(row.group_map or {})
        for supplier_group, ours in body.group_map.items():
            k = svc.key(supplier_group)
            if not k:
                continue
            clean = (ours or "").strip()
            if clean:
                if len(clean) > 60:
                    raise HTTPException(status_code=422, detail="That group name is too long.")
                merged[k] = clean
            else:
                merged.pop(k, None)
        row.group_map = merged
    if body.add_automatically is not None:
        row.add_automatically = body.add_automatically
    if body.mark_in_stock is not None:
        row.mark_in_stock = body.mark_in_stock
    session.flush()
    return _defaults_out(row)


# --------------------------------------------------------------------------
# update item prices
# --------------------------------------------------------------------------


def _changing(
    session: SessionDep, tenant_id: str, catalog_id: str, body: SelectionIn
) -> tuple[int, list[tuple[Item, SupplierCatalogItem]]]:
    ids = [r[0] for r in _label_rows(session, tenant_id, catalog_id, body)]  # type: ignore[arg-type]
    rows: list[SupplierCatalogItem] = []
    for i in range(0, len(ids), 500):
        rows += list(
            session.scalars(
                select(SupplierCatalogItem).where(
                    SupplierCatalogItem.id.in_(ids[i : i + 500]),
                    SupplierCatalogItem.item_id.is_not(None),
                )
            )
        )
    items = {
        it.id: it
        for it in session.scalars(
            select(Item).where(Item.id.in_([r.item_id for r in rows if r.item_id]))
        )
    } if rows else {}
    out: list[tuple[Item, SupplierCatalogItem]] = []
    for r in rows:
        it = items.get(r.item_id or "")
        if it is None or it.merged_into_id is not None:
            continue
        now = Decimal(str(it.default_rate)).quantize(Decimal("0.01")) if it.default_rate else None
        if now != Decimal(str(r.sell_price)).quantize(Decimal("0.01")):
            out.append((it, r))
    return len(rows), out


@router.post("/{catalog_id}/prices/preview", response_model=PricesPreviewOut)
def prices_preview(
    catalog_id: str, body: SelectionIn, session: SessionDep, user: CatalogUser
) -> PricesPreviewOut:
    _get_catalog(session, user.tenant_id, catalog_id)
    total, changing = _changing(session, user.tenant_id, catalog_id, body)
    return PricesPreviewOut(
        total=total,
        changing=len(changing),
        examples=[
            f"{it.name}: {it.default_rate or 'no price'} to {r.sell_price}"
            for it, r in changing[:5]
        ],
    )


@router.post("/{catalog_id}/prices/apply", response_model=PricesApplyOut)
def prices_apply(
    catalog_id: str, body: SelectionIn, session: SessionDep, user: CatalogWriteUser
) -> PricesApplyOut:
    """Set the selling rate of the chosen items that are in your item list to this price list's
    selling price. Explicit: a margin change on a price list never touches your items by itself."""
    _get_catalog(session, user.tenant_id, catalog_id)
    _total, changing = _changing(session, user.tenant_id, catalog_id, body)
    for it, r in changing:
        it.default_rate = float(r.sell_price)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="supplier_catalog",
        entity_id=catalog_id,
        action="update_item_prices",
        after={"updated": len(changing)},
    )
    session.flush()
    return PricesApplyOut(updated=len(changing))


# --------------------------------------------------------------------------
# undo "add to items"
# --------------------------------------------------------------------------


@router.post("/{catalog_id}/promote/undo", response_model=PromoteUndoOut)
def promote_undo(
    catalog_id: str, body: PromoteUndoIn, session: SessionDep, user: CatalogWriteUser
) -> PromoteUndoOut:
    """Remove items that an "add to items" just created. Only items that are still untouched are
    removed: one that is on a document, was sent to Tally or was archived stays."""
    _get_catalog(session, user.tenant_id, catalog_id)
    ids = list(dict.fromkeys(body.item_ids))
    used: set[str] = set()
    for table_name, col in (("invoice_line", "item_id"), ("inward_bill_line", "matched_item_id")):
        table = Item.metadata.tables.get(table_name)
        if table is None or col not in table.c:
            continue
        for i in range(0, len(ids), 500):
            used |= {
                r[0]
                for r in session.execute(
                    select(table.c[col]).where(table.c[col].in_(ids[i : i + 500])).distinct()
                )
            }
    removable: list[Item] = []
    found = 0
    for i in range(0, len(ids), 500):
        for it in session.scalars(
            select(Item).where(Item.id.in_(ids[i : i + 500]), Item.tenant_id == user.tenant_id)
        ):
            found += 1
            if (
                it.source == "catalog"
                and it.times_billed == 0
                and it.tally_status == "none"
                and it.tally_guid is None
                and it.id not in used
            ):
                removable.append(it)
    for i in range(0, len(removable), 500):
        chunk = removable[i : i + 500]
        detach_catalog_links(session, [it.id for it in chunk])
        for it in chunk:  # the ORM, so an item's own rows (aliases, ...) go with it
            session.delete(it)
    removed, kept = len(removable), found - len(removable)
    audit.record(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        entity="supplier_catalog",
        entity_id=catalog_id,
        action="undo_add_to_items",
        after={"removed": removed, "kept": kept},
    )
    session.flush()
    return PromoteUndoOut(removed=removed, kept=kept)


# --------------------------------------------------------------------------
# replace a row's photo
# --------------------------------------------------------------------------


@router.post("/{catalog_id}/items/{row_id}/photo")
def replace_photo(
    catalog_id: str,
    row_id: str,
    session: SessionDep,
    user: CatalogWriteUser,
    storage: Annotated[CatalogStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
) -> dict[str, bool]:
    """Use another picture for this row (the PDF's crop was wrong or missing). When the row is
    already in your item list, that item gets the new photo too."""
    _get_catalog(session, user.tenant_id, catalog_id)
    row = _get_item(session, user.tenant_id, catalog_id, row_id)
    data = file.file.read(media_svc.MAX_UPLOAD_BYTES + 1)
    if len(data) > media_svc.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That photo is too large.")
    try:
        asset = media_svc.ingest(
            session, storage, user.tenant_id, data, source="upload", user_id=user.id
        )
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except (media_svc.ImageRejected, UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="That is not a usable picture.") from exc
    img.thumbnail((1200, 1200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    jpeg = buf.getvalue()
    sha = hashlib.sha256(jpeg).hexdigest()
    key = image_key(user.tenant_id, catalog_id, sha, "jpg")
    storage.put(key, jpeg, "image/jpeg")
    row.image_key, row.image_sha256 = key, sha
    row.image_flag = None
    row.image_w, row.image_h = img.size
    if row.item_id:
        item = session.get(Item, row.item_id)
        if item is not None:
            media_svc.set_item_photo(session, item, asset)
    session.flush()
    return {"ok": True}

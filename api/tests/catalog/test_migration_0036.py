"""Migration 0036's backfill: every legacy catalog row gets one locked product."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import CatalogProduct, Party, SupplierCatalog, SupplierCatalogItem

_PATH = Path(__file__).parents[2] / "alembic" / "versions" / "0036_catalog_product.py"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("mig0036", _PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _catalog(session: Session, tenant_id: str, sha: str, supplier: str | None) -> SupplierCatalog:
    c = SupplierCatalog(
        tenant_id=tenant_id,
        title=sha[:3],
        source_filename="x.pdf",
        source_sha256=sha,
        code_prefix="GL",
        supplier_party_id=supplier,
    )
    session.add(c)
    session.flush()
    return c


def _row(
    tenant_id: str, cid: str, sc: str, code: str, name: str = "Juice  Glass"
) -> SupplierCatalogItem:
    return SupplierCatalogItem(
        tenant_id=tenant_id,
        catalog_id=cid,
        page_no=1,
        position=1,
        supplier_code=sc,
        code=code,
        name_raw="RAW",
        display_name=name,
        cost_price=10,
        sell_price=10,
    )


def test_backfill_creates_one_locked_product_per_row(session: Session, tenant_id: str) -> None:
    sup = Party(tenant_id=tenant_id, legal_name="Sup", legal_name_normalized="sup", role="supplier")
    session.add(sup)
    session.flush()
    a = _catalog(session, tenant_id, "a" * 64, sup.id)
    b = _catalog(session, tenant_id, "b" * 64, sup.id)
    loose = _catalog(session, tenant_id, "c" * 64, None)
    session.add_all(
        [
            _row(tenant_id, a.id, "Z1", "GL-000001"),
            _row(tenant_id, a.id, "Z2", "GL-000002", "Beer Mug"),
            _row(tenant_id, b.id, "Z1", "GL-000003"),  # same supplier + code in a second catalog
            _row(tenant_id, loose.id, "Z1", "GL-000004"),
        ]
    )
    session.flush()

    n = _load().backfill_products(session.connection())
    assert n == 4
    session.expire_all()

    prods = {p.code: p for p in session.query(CatalogProduct).filter_by(tenant_id=tenant_id)}
    assert set(prods) == {"GL-000001", "GL-000002", "GL-000003", "GL-000004"}
    assert all(p.code_locked for p in prods.values())  # codes already in use never change
    assert prods["GL-000001"].supplier_party_id == sup.id
    # the second catalog's duplicate (supplier, code) cannot hold the key twice
    assert prods["GL-000003"].supplier_party_id is None
    assert prods["GL-000004"].supplier_party_id is None
    assert prods["GL-000001"].name_normalized == "juice glass"

    for row in session.query(SupplierCatalogItem).filter_by(tenant_id=tenant_id):
        assert row.product_id is not None
        assert session.get(CatalogProduct, row.product_id).code == row.code


def test_backfill_with_nothing_to_do(session: Session, tenant_id: str) -> None:
    assert _load().backfill_products(session.connection()) == 0

"""Code numbers: sequential per (tenant, prefix), never reused; unique per tenant on products."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CatalogProduct, SupplierCatalog, SupplierCatalogItem, Tenant
from app.services.catalog.codes import allocate_codes, firm_prefix, format_code, normalize_prefix
from tests.catalog.conftest import register, tenant_id_of


def _tenant(session: Session, tenant_id: str) -> Tenant:
    t = session.get(Tenant, tenant_id)
    assert t is not None
    return t


def test_allocation_is_sequential_and_continues(session: Session, tenant_id: str) -> None:
    t = _tenant(session, tenant_id)
    assert allocate_codes(session, t, 3) == ["100001", "100002", "100003"]
    assert allocate_codes(session, t, 2) == ["100004", "100005"]


def test_zero_count_allocates_nothing(session: Session, tenant_id: str) -> None:
    t = _tenant(session, tenant_id)
    assert allocate_codes(session, t, 0) == []
    assert allocate_codes(session, t, 1) == ["100001"]  # counter untouched


def test_negative_count_rejected(session: Session, tenant_id: str) -> None:
    with pytest.raises(ValueError):
        allocate_codes(session, _tenant(session, tenant_id), -1)


def test_tenants_are_independent(session: Session, client: TestClient) -> None:
    a = tenant_id_of(client, register(client, "ta@catalog.example.com"))
    b = tenant_id_of(client, register(client, "tb@catalog.example.com"))
    assert allocate_codes(session, _tenant(session, a), 2) == ["100001", "100002"]
    assert allocate_codes(session, _tenant(session, b), 1) == ["100001"]


def test_the_firm_prefix_goes_in_front_and_changing_it_keeps_the_counter(
    session: Session, tenant_id: str
) -> None:
    t = _tenant(session, tenant_id)
    t.catalog_code_prefix = "ks"
    assert firm_prefix(t) == "KS"
    assert allocate_codes(session, t, 2) == ["KS100001", "KS100002"]
    t.catalog_code_prefix = None
    assert allocate_codes(session, t, 1) == ["100003"]  # one running number, prefix or not


def test_codes_run_past_six_digits() -> None:
    assert format_code("", 999999) == "999999"
    assert format_code("", 1000000) == "1000000"
    assert format_code("KS", 100234) == "KS100234"


def test_normalize() -> None:
    assert normalize_prefix(" gl ") == "GL"
    assert normalize_prefix("G") is None
    assert normalize_prefix(None) is None


# --- the DB backstop ---------------------------------------------------------


def _catalog(session: Session, tenant_id: str, sha: str = "a" * 64) -> SupplierCatalog:
    c = SupplierCatalog(
        tenant_id=tenant_id,
        title="Glassware",
        source_filename="g.pdf",
        source_sha256=sha,
    )
    session.add(c)
    session.flush()
    return c


def _item(tenant_id: str, catalog_id: str, supplier_code: str, code: str) -> SupplierCatalogItem:
    return SupplierCatalogItem(
        tenant_id=tenant_id,
        catalog_id=catalog_id,
        page_no=1,
        position=1,
        supplier_code=supplier_code,
        code=code,
        name_raw="RAW",
        display_name="Raw",
        cost_price=190,
        sell_price=190,
    )


def _product(tenant_id: str, code: str, supplier_code: str = "A1") -> CatalogProduct:
    return CatalogProduct(
        tenant_id=tenant_id,
        supplier_code=supplier_code,
        code=code,
        display_name="Raw",
        name_normalized="raw",
    )


def test_duplicate_product_code_in_tenant_is_rejected(session: Session, tenant_id: str) -> None:
    session.add(_product(tenant_id, "100001", "A1"))
    session.flush()
    session.add(_product(tenant_id, "100001", "A2"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_duplicate_supplier_code_in_catalog_is_rejected(session: Session, tenant_id: str) -> None:
    c = _catalog(session, tenant_id)
    session.add(_item(tenant_id, c.id, "A1", "100001"))
    session.flush()
    session.add(_item(tenant_id, c.id, "A1", "100002"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_same_code_may_appear_in_two_catalogs(session: Session, tenant_id: str) -> None:
    # one product can be offered in several catalogs: the code is not unique per row any more
    a = _catalog(session, tenant_id, "d" * 64)
    b = _catalog(session, tenant_id, "e" * 64)
    session.add(_item(tenant_id, a.id, "A1", "100001"))
    session.add(_item(tenant_id, b.id, "A1", "100001"))
    session.flush()


def test_same_file_twice_is_the_same_catalog(session: Session, tenant_id: str) -> None:
    _catalog(session, tenant_id, "b" * 64)
    with pytest.raises(IntegrityError):
        _catalog(session, tenant_id, "b" * 64)
    session.rollback()


def test_deleting_a_catalog_removes_its_items(session: Session, tenant_id: str) -> None:
    c = _catalog(session, tenant_id, "c" * 64)
    c.items.append(_item(tenant_id, c.id, "A1", "100001"))
    session.flush()
    session.delete(c)
    session.flush()
    assert session.query(SupplierCatalogItem).filter_by(tenant_id=tenant_id).count() == 0

"""Item-code allocation: sequential, per (tenant, prefix), never reused."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SupplierCatalog, SupplierCatalogItem, Tenant
from app.services.catalog.codes import (
    allocate_codes,
    derive_prefix,
    format_code,
    normalize_prefix,
    resolve_prefix,
)
from tests.catalog.conftest import register, tenant_id_of


def test_allocation_is_sequential_and_continues(session: Session, tenant_id: str) -> None:
    first = allocate_codes(session, tenant_id, "GL", 3)
    second = allocate_codes(session, tenant_id, "GL", 2)
    assert first == ["GL-000001", "GL-000002", "GL-000003"]
    assert second == ["GL-000004", "GL-000005"]


def test_prefixes_are_independent(session: Session, tenant_id: str) -> None:
    assert allocate_codes(session, tenant_id, "GL", 2) == ["GL-000001", "GL-000002"]
    assert allocate_codes(session, tenant_id, "ST", 1) == ["ST-000001"]
    assert allocate_codes(session, tenant_id, "GL", 1) == ["GL-000003"]


def test_tenants_are_independent(session: Session, client: TestClient) -> None:
    a = tenant_id_of(client, register(client, "ta@catalog.example.com"))
    b = tenant_id_of(client, register(client, "tb@catalog.example.com"))
    assert allocate_codes(session, a, "GL", 2) == ["GL-000001", "GL-000002"]
    assert allocate_codes(session, b, "GL", 1) == ["GL-000001"]


def test_prefix_is_normalised_so_gl_and_GL_share_a_counter(
    session: Session, tenant_id: str
) -> None:
    assert allocate_codes(session, tenant_id, "gl", 1) == ["GL-000001"]
    assert allocate_codes(session, tenant_id, "GL", 1) == ["GL-000002"]


def test_zero_count_allocates_nothing(session: Session, tenant_id: str) -> None:
    assert allocate_codes(session, tenant_id, "GL", 0) == []
    assert allocate_codes(session, tenant_id, "GL", 1) == ["GL-000001"]  # counter untouched


@pytest.mark.parametrize("bad", ["", "G", "TOOLONGPFX", "G-L", "G L", "ग्ल"])
def test_invalid_prefix_rejected(session: Session, tenant_id: str, bad: str) -> None:
    with pytest.raises(ValueError):
        allocate_codes(session, tenant_id, bad, 1)


def test_negative_count_rejected(session: Session, tenant_id: str) -> None:
    with pytest.raises(ValueError):
        allocate_codes(session, tenant_id, "GL", -1)


def test_codes_past_six_digits_still_unique() -> None:
    assert format_code("GL", 999999) == "GL-999999"
    assert format_code("GL", 1000000) == "GL-1000000"


def test_normalize_and_derive() -> None:
    assert normalize_prefix(" gl ") == "GL"
    assert normalize_prefix("G") is None
    assert normalize_prefix(None) is None
    assert derive_prefix("Glassware Stock") == "GL"
    assert derive_prefix("  a-b c") == "AB"
    assert derive_prefix("7") == "CT"
    assert derive_prefix("") == "CT"


def test_resolve_prefix_precedence(session: Session, tenant_id: str) -> None:
    tenant = session.get(Tenant, tenant_id)
    assert tenant is not None
    # nothing set -> derived from the title
    assert resolve_prefix(tenant, "Glassware Stock") == "GL"
    # tenant default beats derivation
    tenant.catalog_code_prefix = "KS"
    assert resolve_prefix(tenant, "Glassware Stock") == "KS"
    # explicit request beats the tenant default
    assert resolve_prefix(tenant, "Glassware Stock", "bw") == "BW"
    # an invalid request falls through to the next source
    assert resolve_prefix(tenant, "Glassware Stock", "!") == "KS"


# --- the DB backstop ---------------------------------------------------------


def _catalog(session: Session, tenant_id: str, sha: str = "a" * 64) -> SupplierCatalog:
    c = SupplierCatalog(
        tenant_id=tenant_id,
        title="Glassware",
        source_filename="g.pdf",
        source_sha256=sha,
        code_prefix="GL",
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


def test_duplicate_code_in_tenant_is_rejected(session: Session, tenant_id: str) -> None:
    c = _catalog(session, tenant_id)
    session.add(_item(tenant_id, c.id, "A1", "GL-000001"))
    session.flush()
    session.add(_item(tenant_id, c.id, "A2", "GL-000001"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_duplicate_supplier_code_in_catalog_is_rejected(session: Session, tenant_id: str) -> None:
    c = _catalog(session, tenant_id)
    session.add(_item(tenant_id, c.id, "A1", "GL-000001"))
    session.flush()
    session.add(_item(tenant_id, c.id, "A1", "GL-000002"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_same_file_twice_is_the_same_catalog(session: Session, tenant_id: str) -> None:
    _catalog(session, tenant_id, "b" * 64)
    with pytest.raises(IntegrityError):
        _catalog(session, tenant_id, "b" * 64)
    session.rollback()


def test_deleting_a_catalog_removes_its_items(session: Session, tenant_id: str) -> None:
    c = _catalog(session, tenant_id, "c" * 64)
    c.items.append(_item(tenant_id, c.id, "A1", "GL-000001"))
    session.flush()
    session.delete(c)
    session.flush()
    assert session.query(SupplierCatalogItem).filter_by(tenant_id=tenant_id).count() == 0

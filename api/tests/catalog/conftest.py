"""Shared fixtures for the supplier-catalog tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Tenant
from app.services.catalog.storage import LocalStorage, get_storage

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "catalog"
SAMPLE_PDF = FIXTURE_DIR / "glassware-2pp.pdf"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def register(client: TestClient, email: str = "owner@catalog.example.com") -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sample Traders", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    token: str = r.json()["access_token"]
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def tenant_id_of(client: TestClient, token: str) -> str:
    tid: str = client.get("/api/auth/me", headers=auth(token)).json()["tenant_id"]
    return tid


def enable_catalog_flag(client: TestClient, token: str) -> str:
    """Flip tenant.ext_supplier_catalog for the caller's tenant; return its id."""
    from app.db import SessionLocal

    tid = tenant_id_of(client, token)
    with SessionLocal() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.id == tid))
        assert tenant is not None
        tenant.ext_supplier_catalog = True
        s.commit()
    return tid


@pytest.fixture(autouse=True)
def _local_storage(tmp_path: Path) -> Iterator[LocalStorage]:
    """Every catalog test stores photos in its own tmp dir, never R2 or /data."""
    st = LocalStorage(tmp_path / "catalog-store")
    app.dependency_overrides[get_storage] = lambda: st
    yield st
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
def storage(_local_storage: LocalStorage) -> LocalStorage:
    return _local_storage


@pytest.fixture
def catalog_client(client: TestClient) -> tuple[TestClient, dict[str, str], str]:
    """(client, auth headers, tenant id) for a tenant with the flag ON."""
    token = register(client)
    tid = enable_catalog_flag(client, token)
    return client, auth(token), tid


@pytest.fixture
def tenant_id(client: TestClient) -> str:
    """A bare tenant (flag off) for service-level tests."""
    return tenant_id_of(client, register(client, "svc@catalog.example.com"))


def seed_hsn(*codes: str) -> None:
    """Put codes in the HSN reference list (an item's HSN is a foreign key to it, and the test
    database enforces foreign keys like production does)."""
    from app.db import SessionLocal
    from app.models import HsnCode

    with SessionLocal() as s:
        for c in codes:
            if s.get(HsnCode, c) is None:
                s.add(HsnCode(code=c, description=f"Test code {c}", chapter=c[:2]))
        s.commit()

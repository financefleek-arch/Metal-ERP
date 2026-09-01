"""Shared fixtures for the inward-bill tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Tenant

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "inward"
SUGAL_PDF = FIXTURE_DIR / "sugal-foods-INV2526-5667.pdf"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sugal_pdf_bytes() -> bytes:
    assert SUGAL_PDF.exists(), f"missing fixture: {SUGAL_PDF}"
    return SUGAL_PDF.read_bytes()


def register(client: TestClient, email: str = "acct@rajdeep.example.com") -> str:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Rajdeep Stores", "email": email, "password": "s3cret-pass"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def enable_inward_flag(client: TestClient, token: str) -> None:
    """Flip tenant.ext_inward_import for the caller's tenant (no admin UI yet)."""
    from app.db import SessionLocal

    me = client.get("/api/auth/me", headers=auth(token)).json()
    with SessionLocal() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.id == me["tenant_id"]))
        assert tenant is not None
        tenant.ext_inward_import = True
        s.commit()


@pytest.fixture
def inward_client(client: TestClient) -> tuple[TestClient, dict[str, str]]:
    """A client with a registered tenant that has ext_inward_import ON."""
    token = register(client)
    enable_inward_flag(client, token)
    return client, auth(token)


@pytest.fixture
def seeded_hsn() -> None:
    """Seed the HSN reference row the Sugal Foods lines use (21069092)."""
    from app.db import SessionLocal
    from app.models import HsnCode

    with SessionLocal() as s:
        if s.get(HsnCode, "21069092") is None:
            s.add(
                HsnCode(
                    code="21069092",
                    description="Food preparations n.e.s. — sharbat / syrup",
                    chapter="21",
                    default_gst_rate=18.0,
                )
            )
            s.commit()

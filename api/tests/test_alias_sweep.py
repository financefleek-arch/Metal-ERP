"""alias_sweep: retires stale `learned` aliases, spares document-derived ones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Item, ItemAlias
from app.models._mixins import AliasSource
from app.services.catalogue.alias_sweep import sweep_stale_aliases


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient) -> tuple[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"firm_name": "Sethia Metal Store", "email": "sweep@x.example.com",
              "password": "s3cret-pass"},
    )
    assert r.status_code == 201
    tok = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()
    return tok, me["tenant_id"]


def test_sweep(client: TestClient, session) -> None:  # type: ignore[no-untyped-def]
    _tok, tenant_id = _register(client)
    it = Item(
        tenant_id=tenant_id, name="Base Item", name_normalized="base item",
        rate_mode="piece",
    )
    session.add(it)
    session.flush()

    old = datetime.now(UTC) - timedelta(days=200)
    fresh = datetime.now(UTC) - timedelta(days=5)

    rows = [
        ItemAlias(tenant_id=tenant_id, item_id=it.id, alias_text="stale learned",
                  alias_normalized="stale learned", source=AliasSource.learned,
                  last_used_at=old),
        ItemAlias(tenant_id=tenant_id, item_id=it.id, alias_text="fresh learned",
                  alias_normalized="fresh learned", source=AliasSource.learned,
                  last_used_at=fresh),
        ItemAlias(tenant_id=tenant_id, item_id=it.id, alias_text="null learned",
                  alias_normalized="null learned", source=AliasSource.learned,
                  last_used_at=None),
        ItemAlias(tenant_id=tenant_id, item_id=it.id, alias_text="old purchase",
                  alias_normalized="old purchase",
                  source=AliasSource.auto_from_purchase, last_used_at=old),
        ItemAlias(tenant_id=tenant_id, item_id=it.id, alias_text="old invoice",
                  alias_normalized="old invoice",
                  source=AliasSource.auto_from_invoice, last_used_at=old),
    ]
    session.add_all(rows)
    session.commit()

    # dry-run touches nothing
    would = sweep_stale_aliases(session, days=90, dry_run=True)
    assert len(would) == 2
    assert session.scalar(select(ItemAlias).where(ItemAlias.alias_normalized == "stale learned"))

    deleted = sweep_stale_aliases(session, days=90)
    session.commit()
    assert set(deleted) == {rows[0].id, rows[2].id}

    remaining = {
        a.alias_normalized
        for a in session.scalars(select(ItemAlias).where(ItemAlias.tenant_id == tenant_id)).all()
    }
    assert remaining == {"fresh learned", "old purchase", "old invoice"}

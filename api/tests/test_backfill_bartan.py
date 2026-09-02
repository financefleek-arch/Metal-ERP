"""backfill_bartan: seeds synonyms into an existing tenant and re-normalizes
the catalogue, flagging collisions instead of blindly updating."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Item, Synonym, Tenant
from app.services.catalogue.backfill_bartan import apply_tenant, plan_tenant, run


@pytest.fixture
def tenant(session):  # type: ignore[no-untyped-def]
    t = Tenant(legal_name="Bartan Bhandar")
    session.add(t)
    session.flush()
    return t


def _add_item(session, tenant_id: str, name: str, norm: str) -> Item:  # type: ignore[no-untyped-def]
    it = Item(
        tenant_id=tenant_id, name=name, name_normalized=norm, rate_mode="piece"
    )
    session.add(it)
    session.flush()
    return it


def test_seeds_and_renormalizes_non_colliding(session, tenant) -> None:  # type: ignore[no-untyped-def]
    # stored under the OLD dictionary (no bartan synonyms)
    it = _add_item(session, tenant.id, "Pital Balti No 3", "pital balti no 3")

    res = plan_tenant(session, tenant.id)
    assert res.synonyms_added > 0
    assert not res.collisions
    assert len(res.safe_changes) == 1
    assert res.safe_changes[0].new_key == "brass bucket no 3"

    n = apply_tenant(session, res)
    session.flush()
    assert n == 1
    session.refresh(it)
    assert it.name_normalized == "brass bucket no 3"
    # synonym rows really landed
    assert session.scalar(
        select(Synonym).where(
            Synonym.tenant_id == tenant.id, Synonym.from_token == "balti"
        )
    )


def test_collision_is_flagged_not_applied(session, tenant) -> None:  # type: ignore[no-untyped-def]
    # two items that converge on "brass bucket no 3" once bartan syns apply
    a = _add_item(session, tenant.id, "Pital Balti No 3", "pital balti no 3")
    b = _add_item(session, tenant.id, "Brass Bucket No 3", "brass bucket no 3")

    res = plan_tenant(session, tenant.id)
    # 'a' would move onto a key 'b' already holds -> collision, no safe change
    assert res.collisions
    assert res.safe_changes == []

    apply_tenant(session, res)
    session.flush()
    session.refresh(a)
    session.refresh(b)
    assert a.name_normalized == "pital balti no 3"  # untouched
    assert b.name_normalized == "brass bucket no 3"


def test_run_all_tenants_report_only(session, tenant) -> None:  # type: ignore[no-untyped-def]
    _add_item(session, tenant.id, "Kadhai 10", "kadhai 10")
    results = run(session, apply=False)
    mine = [r for r in results if r.tenant_id == tenant.id][0]
    assert mine.applied == 0
    assert any(c.new_key == "wok 10" for c in mine.changes)
    # nothing written in report mode
    it = session.scalar(select(Item).where(Item.tenant_id == tenant.id))
    assert it.name_normalized == "kadhai 10"

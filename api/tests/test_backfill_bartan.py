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
    # stored under the OLD dictionary (spelling variant not yet collapsed)
    it = _add_item(session, tenant.id, "07 Fancy Mor Jhoola", "07 fancy mor jhoola")

    res = plan_tenant(session, tenant.id)
    assert res.synonyms_added > 0
    assert not res.collisions
    assert len(res.safe_changes) == 1
    assert res.safe_changes[0].new_key == "07 fancy mor jhula"

    n = apply_tenant(session, res)
    session.flush()
    assert n == 1
    session.refresh(it)
    assert it.name_normalized == "07 fancy mor jhula"
    # synonym rows really landed
    assert session.scalar(
        select(Synonym).where(
            Synonym.tenant_id == tenant.id, Synonym.from_token == "jhoola"
        )
    )


def test_collision_is_flagged_not_applied(session, tenant) -> None:  # type: ignore[no-untyped-def]
    # two items that converge on "ss kadai 10" once the spelling syns apply
    a = _add_item(session, tenant.id, "SS Kadhai 10", "ss kadhai 10")
    b = _add_item(session, tenant.id, "SS Kadai 10", "ss kadai 10")

    res = plan_tenant(session, tenant.id)
    # 'a' would move onto a key 'b' already holds -> collision, no safe change
    assert res.collisions
    assert res.safe_changes == []
    col = res.collisions["item:ss kadai 10"]
    assert col.incumbent is not None and col.incumbent[0] == b.id
    assert [c.row_id for c in col.moving] == [a.id]

    apply_tenant(session, res)
    session.flush()
    session.refresh(a)
    session.refresh(b)
    assert a.name_normalized == "ss kadhai 10"  # untouched
    assert b.name_normalized == "ss kadai 10"


def test_run_all_tenants_report_only(session, tenant) -> None:  # type: ignore[no-untyped-def]
    _add_item(session, tenant.id, "Karahi 10", "karahi 10")
    results = run(session, apply=False)
    mine = [r for r in results if r.tenant_id == tenant.id][0]
    assert mine.applied == 0
    assert any(c.new_key == "kadai 10" for c in mine.changes)
    # nothing written in report mode
    it = session.scalar(select(Item).where(Item.tenant_id == tenant.id))
    assert it.name_normalized == "karahi 10"
